import os, re, logging
from logging.handlers import RotatingFileHandler
from time import sleep
from dotenv import load_dotenv
from flask import Flask, request
from twilio.rest import Client
import openai
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Cc, Content

# ================== ENV ==================
load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")

STATUS_CALLBACK_URL = os.environ.get("STATUS_CALLBACK_URL")

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
LEADS_NOTIFY_TO = os.environ.get("LEADS_NOTIFY_TO")
LEADS_NOTIFY_FROM = os.environ.get("LEADS_NOTIFY_FROM", "info@montessorixaltepec.edu.mx")
LEADS_NOTIFY_CC = os.environ.get("LEADS_NOTIFY_CC", "")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ================== LOGGING ==================
logger = logging.getLogger("montessori-bot")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
file_handler = RotatingFileHandler("twilio_status.log", maxBytes=1_000_000, backupCount=3)
logger.addHandler(console_handler); logger.addHandler(file_handler)

# ================== PROMPT ==================
PROMPT = """
Eres un asistente digital de la Escuela Montessori Xaltepec. Tu función es brindar información breve y clara sobre el colegio, sus niveles y proceso de admisión, y ayudar a los padres de familia a agendar una visita.

Reglas:
- Sé cordial, empático y profesional (máximo 70 palabras por respuesta).
- Si te saludan o piden informes, responde con una bienvenida cálida, por ejemplo:
  "¡Hola! Soy el asistente de admisiones de Montessori Xaltepec. Contamos con preescolar, primaria y secundaria. ¿Deseas agendar una visita para conocer nuestras instalaciones?"
- Menciona solo información real del colegio:
  *Niveles educativos:* Comunidad Infantil (Maternal), Casa de los Niños (Preescolar), Taller 1 y 2 (Primaria), Comunidad de Adolescentes (Secundaria).
  *Horarios:* De lunes a viernes, entre 7:45 y 14:40 hrs (varía por nivel).
  *Método:* Montessori auténtico, con supervisión AMI y SEP.
  *Clases complementarias:* Inglés (certificación Cambridge), Deportes, Música.
  *Proceso de admisión:* Entrevista + visita, días de prueba, y decisión final.
  *Descuentos:* 10% en inscripción para familias Montessori, bono por referidos y hermanos.
- Si preguntan por costos, menciona que los costos actualizados dependen del nivel y que se explican durante la visita.
- Si preguntan por uniformes o brunch, responde según el nivel.
- Si preguntan por apoyo financiero, aclara que solo aplica para alumnos inscritos con más de un ciclo completo.
- Para agendar visita: pide nombre completo, correo electrónico y día/hora preferida.
- Siempre que el usuario muestre interés, guíalo hacia agendar una visita con el mensaje:
  "Podemos coordinar una visita para que conozcas el colegio y el método Montessori en acción. ¿Deseas que te ayude a agendarla?"
- No incluyas imágenes ni links. No inventes información.
"""

# ================== NIVELES EDUCATIVOS ==================
PRODUCTOS = {
    "preescolar": {
        "nombre": "Casa de los Niños (Preescolar)",
        "descripcion": "Ambiente Montessori para niños de 3 a 6 años, con horario de 8:00 a 14:00 hrs."
    },
    "primaria": {
        "nombre": "Taller 1 y 2 (Primaria)",
        "descripcion": "Formación integral Montessori para niños de 6 a 12 años, con clases de inglés, música y deportes."
    },
    "secundaria": {
        "nombre": "Comunidad de Adolescentes (Secundaria)",
        "descripcion": "Ambiente para jóvenes de 12 a 15 años, con enfoque en autonomía, trabajo en equipo y vida práctica."
    }
}

# ================== SESIONES ==================
SESSIONS = {}  # { from_number: {stage, nivel, name, email, when, ready_to_notify}}

def ensure_session(num: str):
    return SESSIONS.setdefault(num, {
        "stage":"idle",
        "nivel":None,
        "name":None,
        "email":None,
        "when":None,
        "ready_to_notify":False
    })

# ================== HELPERS ==================
def get_ai_reply(user_message: str) -> str:
    try:
        r = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role":"system","content":PROMPT},{"role":"user","content":user_message}],
            temperature=0.3,
            max_tokens=180
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.exception(f"OpenAI error: {e}")
        return "Ocurrió un error al procesar tu mensaje."

def enviar_texto(to_number: str, body: str):
    try:
        kwargs = dict(from_=TWILIO_WHATSAPP_NUMBER, to=to_number, body=body)
        if STATUS_CALLBACK_URL: kwargs["status_callback"] = STATUS_CALLBACK_URL
        msg = twilio_client.messages.create(**kwargs)
        logger.info(f"✅ Texto SID={msg.sid}")
    except Exception as e:
        logger.exception(f"Twilio texto error: {e}")

def extract_phone(whatsapp_from: str) -> str:
    return whatsapp_from.replace("whatsapp:", "") if whatsapp_from else ""

def looks_like_email(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))

def is_greeting(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(x) for x in ["hola","buenas","buen día","buen dia","hey","holi"]) or t in {"hi","hello","saludos"}

def want_visit(text: str) -> bool:
    t = text.lower()
    keys = ["agendar","agenda","visita","cita","tour","recorrido","ver escuela","quiero conocer","visitar"]
    return any(k in t for k in keys)

def parse_nivel(text: str) -> str | None:
    t = text.lower()
    if "preescolar" in t or "casa de los niños" in t: return "preescolar"
    if "primaria" in t or "taller" in t: return "primaria"
    if "secundaria" in t or "adolescente" in t: return "secundaria"
    return None

# ================== SENDGRID ==================
def enviar_correo_lead(nombre: str, email: str, phone: str, nivel: str, when_str: str | None):
    if not SENDGRID_API_KEY or not LEADS_NOTIFY_TO:
        logger.warning("⚠️ SendGrid no configurado: faltan SENDGRID_API_KEY o LEADS_NOTIFY_TO")
        return
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        to_list = [To(LEADS_NOTIFY_TO)]
        cc_list = [Cc(a.strip()) for a in LEADS_NOTIFY_CC.split(",") if a.strip()] if LEADS_NOTIFY_CC else None
        from_email = Email(LEADS_NOTIFY_FROM)

        subject = f"Nuevo lead – {nivel}"
        when_html = f"<p><b>Horario preferido:</b> {when_str}</p>" if when_str else ""
        when_txt  = f"Horario preferido: {when_str}\n" if when_str else ""

        html = f"""
        <h2>Nuevo lead de visita Montessori Xaltepec</h2>
        <p><b>Nivel:</b> {nivel}</p>
        <p><b>Nombre:</b> {nombre}</p>
        <p><b>Email:</b> <a href="mailto:{email}">{email}</a></p>
        <p><b>Teléfono (WhatsApp):</b> <a href="tel:{phone}">{phone}</a></p>
        {when_html}
        <hr>
        <p>Acción sugerida: contactar y confirmar visita al colegio.</p>
        """

        text = (
            f"Nuevo lead de visita Montessori Xaltepec\n"
            f"Nivel: {nivel}\n"
            f"Nombre: {nombre}\n"
            f"Email: {email}\n"
            f"Teléfono (WhatsApp): {phone}\n"
            f"{when_txt}"
            f"Acción: contactar para confirmar visita.\n"
        )

        message = Mail(from_email=from_email, to_emails=to_list, subject=subject, html_content=html)
        if cc_list:
            for c in cc_list: message.add_cc(c)
        message.add_content(Content("text/plain", text))
        sg.send(message)
        logger.info("✅ Email de lead enviado al asesor.")
    except Exception as e:
        logger.exception(f"❌ Error al enviar correo de lead: {e}")

def on_lead_ready(nombre: str, email: str, phone: str, nivel: str, when_str: str | None):
    logger.info(f"🔔 Lead listo: {nombre} | {email} | {phone} | {nivel} | {when_str}")
    enviar_correo_lead(nombre, email, phone, nivel, when_str)

# ================== STATE MACHINE ==================
def handle_visit_flow(from_number: str, user_message: str, phone: str) -> bool:
    s = ensure_session(from_number)

    if s["stage"] in ("idle","choose_nivel") and want_visit(user_message):
        if not s["nivel"]:
            s["stage"] = "choose_nivel"
            enviar_texto(from_number, "¿Qué nivel te interesa? ¿Preescolar, Primaria o Secundaria?")
            return True
        s["stage"] = "ask_name"
        enviar_texto(from_number, "Excelente. Para agendar la visita, ¿me compartes tu nombre completo?")
        return True

    if s["stage"] == "choose_nivel":
        nivel = parse_nivel(user_message)
        if nivel:
            s["nivel"] = nivel
            s["stage"] = "ask_name"
            enviar_texto(from_number, "Perfecto. ¿Podrías compartirme tu nombre completo?")
        else:
            enviar_texto(from_number, "Por favor indícame si te interesa Preescolar, Primaria o Secundaria.")
        return True

    if s["stage"] == "ask_name":
        name = user_message.strip()
        if 2 <= len(name) <= 80:
            s["name"] = name
            s["stage"] = "ask_email"
            enviar_texto(from_number, "Gracias. ¿Cuál es tu correo electrónico?")
        else:
            enviar_texto(from_number, "Compárteme tu nombre completo para continuar.")
        return True

    if s["stage"] == "ask_email":
        if looks_like_email(user_message):
            s["email"] = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message).group(0)
            s["stage"] = "ask_when"
            enviar_texto(
                from_number,
                f"Listo, {s['name']}. Te contacto al {phone} y {s['email']} para coordinar la visita. "
                "¿Tienes día y horario preferido?"
            )
        else:
            enviar_texto(from_number, "Ese correo no parece válido. Escríbelo así: nombre@dominio.com")
        return True

    if s["stage"] == "ask_when":
        s["when"] = user_message.strip()
        s["stage"] = "closed"
        s["ready_to_notify"] = True
        enviar_texto(from_number, "Excelente, un asesor se pondrá en contacto contigo para confirmar tu visita.")
        nivel_name = PRODUCTOS[s["nivel"]]["nombre"] if s["nivel"] in PRODUCTOS else "nivel educativo"
        on_lead_ready(s["name"], s["email"], phone, nivel_name, s["when"])
        return True

    if s["stage"] == "closed":
        return True

    return False

# ================== FLASK ==================
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "") or ""
    from_number = request.form.get("From", "") or ""
    phone = extract_phone(from_number)
    s = ensure_session(from_number)

    logger.info(f"📩 {from_number}: {user_message}")
    logger.info(f"🧭 state: stage={s['stage']} nivel={s['nivel']}")

    if is_greeting(user_message):
        SESSIONS[from_number] = {
            "stage": "idle", "nivel": None, "name": None, "email": None,
            "when": None, "ready_to_notify": False
        }
        enviar_texto(
            from_number,
            "¡Hola! Soy el asistente de admisiones de Montessori Xaltepec. "
            "Contamos con preescolar, primaria y secundaria. ¿Te gustaría agendar una visita para conocer nuestras instalaciones?"
        )
        return "OK", 200

    detected_nivel = parse_nivel(user_message)
    if s["stage"] in ("idle", "choose_nivel") and detected_nivel:
        s["nivel"] = detected_nivel
        s["stage"] = "idle"
        prod = PRODUCTOS.get(detected_nivel)
        enviar_texto(from_number, f"{prod['descripcion']}")
        sleep(0.3)
        enviar_texto(from_number, "¿Te gustaría agendar una visita para conocer el colegio?")
        return "OK", 200

    if handle_visit_flow(from_number, user_message, phone):
        return "OK", 200

    respuesta_texto = get_ai_reply(user_message)
    enviar_texto(from_number, respuesta_texto)
    return "OK", 200

@app.route("/twilio-status", methods=["POST"])
def twilio_status():
    logger.info(f"📬 Status callback: {dict(request.form)}")
    return "OK", 200

if __name__ == "__main__":
    logger.info("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
