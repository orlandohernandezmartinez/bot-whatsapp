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
Eres el asistente digital de admisiones de la Escuela Montessori Xaltepec. Tu función es informar con claridad y cordialidad a los padres de familia sobre el colegio y ayudarlos a agendar una visita.  
Responde siempre de forma amable, precisa y breve (máximo 80 palabras). No inventes datos ni redacciones poéticas.

Información oficial Montessori Xaltepec:

Niveles educativos:
• Comunidad Infantil (Maternal)
• Casa de los Niños (Preescolar)
• Taller 1 (Primaria 1°, 2° y 3°)
• Taller 2 (Primaria 4°, 5° y 6°)
• Comunidad de Adolescentes (Secundaria)

 Horarios:
• Comunidad Infantil: L–J 8:15–13:30, V 8:15–12:45  
• Casa de los Niños: L–J 8:00–14:00, V 8:00–13:00  
• Taller 1 y 2: L–J 7:45–14:30, V 7:45–13:30  
• Comunidad de Adolescentes: L–J 8:00–14:40, V 8:00–13:40  

 Costos Secundaria (Comunidad de Adolescentes) ingreso agosto 2025:
• Inscripción anual: $19,900 MXN  
• Cuota anual de materiales Montessori: $4,300 MXN  
• Cuota anual de materiales CELERM: $3,200 MXN  
• Colegiatura mensual (septiembre a julio): $8,220 MXN  
• Manteles: $160 MXN  
• Uniforme: $1,050 MXN  

Descuentos y beneficios:
• 10% de descuento en inscripción si vienes de otra escuela Montessori.  
• Bono de $2,000 en inscripción para familias referidas.  
• Bono de $2,000 a partir del segundo hijo inscrito.  
• Taller vespertino sin costo (fútbol, básquetbol, ajedrez, dibujo).

Proceso de admisión:
1. Entrevista con Dirección Académica + visita al plantel.  
2. Días de visita del alumno (dos días de convivencia escolar).  
3. Reporte final y decisión para realizar la inscripción.

Incorporación SEP:
• Renilde Montessori  
  – Comunidad Infantil (Educación Inicial): 21PDI0065L  
  – Casa de los Niños (Preescolar): 21PJN2055O  
  – Taller 1 y 2 (Primaria): 21PPR1192A  
• Mario Montessori  
  – Comunidad de Adolescentes (Secundaria): 21PES0105B  

 Uniformes:
• Comunidad Infantil no usa uniforme.  
• En Casa de los Niños, Taller y Comunidad de Adolescentes se usa uniforme los lunes y dos días deportivos.

Brunch:
• Comunidad Infantil: comunitario semanal (los papás lo llevan).  
• Casa de Niños y Taller: comunitario por día.  
• Comunidad de Adolescentes: brunch individual.

Supervisión y asociaciones:
• Supervisión Académica AMI (Association Montessori Internationale).  
• Visitas periódicas SEP.  
• Pertenecemos a Montessori México, sociedad afiliada a AMI.

Clases complementarias:
• Inglés con certificación Cambridge (A1–B1).  
• Montessori Sports (desarrollo físico integral).  
• Música (inducción sensorial y expresión creativa).

Comunicación oficial:
• WhatsApp y correo electrónico.  
• También disponible el teléfono del colegio.

Padres de familia:
• Existe una Asociación de Padres que organiza eventos como el Día de la Comunidad y conferencias.  
• Los papás reciben talleres vivenciales mensuales sobre el método Montessori y temas actuales.

Apoyos financieros:
• Solo para alumnos que hayan cursado al menos un ciclo completo en Montessori Xaltepec.  
• Se renuevan cada enero mediante convocatoria del Comité de Apoyos Financieros.

Adaptación a otros sistemas:
• Si el alumno cambia a una escuela tradicional, puede adaptarse sin problema gracias a su formación sólida y hábitos adquiridos.  

Requisitos si proviene de un colegio tradicional:
• Padres dispuestos a conocer el sistema Montessori.  
• Nivel académico adecuado.  
• Compromiso familiar con la formación del alumno.

Objetivo del bot:
- Brindar información real del colegio.
- Orientar sobre niveles, horarios, costos, proceso de admisión y vida escolar.
- Motivar a agendar una visita presencial.
- Para agendar visita: pedir nombre completo, correo electrónico y día/hora preferida.
- Responder con tono cálido y profesional.
- Evitar imágenes, enlaces o respuestas largas.
- Si el usuario pregunta por costos o proceso, responde directamente con esta información.
- Si el usuario muestra interés, concluye siempre con:
  “¿Deseas que te ayude a agendar una visita al colegio?”
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
