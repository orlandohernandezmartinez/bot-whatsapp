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
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")  # 'whatsapp:+14155238886' en Sandbox

STATUS_CALLBACK_URL = os.environ.get("STATUS_CALLBACK_URL")  # ej: https://tuapp.railway.app/twilio-status

# SendGrid
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
LEADS_NOTIFY_TO = os.environ.get("LEADS_NOTIFY_TO")                # asesor@tudominio.com
LEADS_NOTIFY_FROM = os.environ.get("LEADS_NOTIFY_FROM", "orlando@vacacapital.com")
LEADS_NOTIFY_CC = os.environ.get("LEADS_NOTIFY_CC", "")            # opcional, coma-separado

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ================== LOGGING ==================
logger = logging.getLogger("coinsa-bot")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
file_handler = RotatingFileHandler("twilio_status.log", maxBytes=1_000_000, backupCount=3)
logger.addHandler(console_handler); logger.addHandler(file_handler)

# ================== PROMPT ==================
PROMPT = """
Eres un asistente inmobiliario digital de COINSA (SOFOM ENR, NL). Sé claro, cordial y breve (máx. 60 palabras).

Reglas:
- Si piden informes/propiedades, primero pregunta si busca COMPRAR o RENTAR.
- Si elige COMPRAR, responde con un tono amable y humano, por ejemplo:
  "¡Con gusto! Te comparto la opción disponible: un edificio en Puerto Escondido, Oaxaca, con 4 pisos y 8 departamentos. El precio es de 800,000 USD."
- Si elige RENTAR, responde en tono similar:
  "¡Perfecto! Tenemos disponible un Pent House en la zona Tec con 2 habitaciones, 2 baños completos, terraza privada, sala y comedor."
- Si piden fotos, indica que puedes enviar una foto y sugiere agendar visita.
- Para agendar visita: pide nombre y correo; el teléfono es el de este chat.
- No incluyas bloques de contacto a menos que lo pidan explícitamente.
"""

# ================== PRODUCTOS ==================
PRODUCTOS = {
    "renta": {
        "nombre": "pent house zona tec (renta)",
        "descripcion": "Pent House en zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.",
        "imagenes": [
            "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/comedor_pjbxyq.jpg"
        ]
    },
    "venta": {
        "nombre": "edificio 4 pisos · 8 deptos (venta) – Puerto Escondido, Oaxaca",
        "descripcion": "Edificio en Puerto Escondido, Oaxaca: 4 pisos, 8 departamentos. Precio: $800,000 USD.",
        "imagenes": [
            "https://res.cloudinary.com/dafozmwvq/image/upload/v1758054049/fachada_tninqu.jpg"
        ]
    }
}

# ================== SESIONES ==================
# stage: idle | choose_mode | ask_name | ask_email | ask_when | closed
# mode: "renta" | "venta" | None
SESSIONS = {}  # { from_number: {stage, mode, name, email, when, ready_to_notify}}

def ensure_session(num: str):
    return SESSIONS.setdefault(num, {
        "stage":"idle",
        "mode":None,
        "name":None,
        "email":None,
        "when":None,
        "ready_to_notify":False
    })

# ================== HELPERS ==================
def optimize(url: str) -> str:
    # Fuerza JPG comprimido y ancho razonable para WhatsApp
    return url.replace("/upload/", "/upload/f_jpg,q_auto,w_1280/")

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

def enviar_imagen(to_number: str, body: str, url: str):
    try:
        url_opt = optimize(url)
        kwargs = dict(from_=TWILIO_WHATSAPP_NUMBER, to=to_number, body=body, media_url=[url_opt])
        if STATUS_CALLBACK_URL: kwargs["status_callback"] = STATUS_CALLBACK_URL
        msg = twilio_client.messages.create(**kwargs)
        logger.info(f"✅ Texto+Imagen SID={msg.sid} -> {url_opt}")
    except Exception as e:
        logger.exception(f"Twilio media error: {e}")

def extract_phone(whatsapp_from: str) -> str:
    return whatsapp_from.replace("whatsapp:", "") if whatsapp_from else ""

def looks_like_email(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))

def is_greeting(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(x) for x in ["hola","buenas","buen día","buen dia","hey","holi"]) or t in {"hi","hello","saludos"}

def want_listings(text: str) -> bool:
    t = text.lower()
    keys = ["propiedades", "propiedad", "informes", "información de propiedades", "qué propiedades", "que propiedades", "inventario", "disponible", "disponibles"]
    return any(k in t for k in keys)

def parse_mode(text: str) -> str | None:
    t = text.lower()
    if "renta" in t or "rentar" in t or "alqu" in t: return "renta"
    if "compra" in t or "comprar" in t or "venta" in t or "vender" in t: return "venta"
    return None

def want_photos(text: str) -> bool:
    t = text.lower()
    keys = ["foto","fotos","imagen","imágenes","imagenes","ver fotos","a ver las fotos","quiero ver las fotos","enséñame","enseñame"]
    return any(k in t for k in keys)

def want_visit(text: str) -> bool:
    t = text.lower()
    keys = ["agendar","agenda","visita","cita","tour","recorrido","verlo","ver la propiedad","quiero ver"]
    return any(k in t for k in keys)

# ================== SENDGRID ==================
def enviar_correo_lead(nombre: str, email: str, phone: str, propiedad: str, when_str: str | None):
    if not SENDGRID_API_KEY or not LEADS_NOTIFY_TO:
        logger.warning("⚠️ SendGrid no configurado: faltan SENDGRID_API_KEY o LEADS_NOTIFY_TO")
        return
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        to_list = [To(LEADS_NOTIFY_TO)]
        cc_list = [Cc(a.strip()) for a in LEADS_NOTIFY_CC.split(",") if a.strip()] if LEADS_NOTIFY_CC else None
        from_email = Email(LEADS_NOTIFY_FROM)

        subject = f"Nuevo lead – {propiedad}"
        phone_safe = phone

        when_html = f"<p><b>Horario preferido:</b> {when_str}</p>" if when_str else ""
        when_txt  = f"Horario preferido: {when_str}\n" if when_str else ""

        html = f"""
        <h2>Nuevo lead</h2>
        <p><b>Propiedad:</b> {propiedad}</p>
        <p><b>Nombre:</b> {nombre}</p>
        <p><b>Email:</b> <a href="mailto:{email}">{email}</a></p>
        <p><b>Teléfono (WhatsApp):</b> <a href="tel:{phone_safe}">{phone_safe}</a></p>
        {when_html}
        <hr>
        <p>Acción sugerida: contactar y confirmar visita.</p>
        """

        text = (
            f"Nuevo lead\n"
            f"Propiedad: {propiedad}\n"
            f"Nombre: {nombre}\n"
            f"Email: {email}\n"
            f"Teléfono (WhatsApp): {phone_safe}\n"
            f"{when_txt}"
            f"Acción: contactar y confirmar visita.\n"
        )

        message = Mail(from_email=from_email, to_emails=to_list, subject=subject, html_content=html)
        if cc_list:
            for c in cc_list: message.add_cc(c)
        message.add_content(Content("text/plain", text))
        sg.send(message)
        logger.info("✅ Email de lead enviado al asesor.")
    except Exception as e:
        logger.exception(f"❌ Error al enviar correo de lead: {e}")

def on_lead_ready(nombre: str, email: str, phone: str, propiedad: str, when_str: str | None):
    logger.info(f"🔔 Lead listo: {nombre} | {email} | {phone} | {propiedad} | {when_str}")
    enviar_correo_lead(nombre, email, phone, propiedad, when_str)

# ================== STATE MACHINE (agendar) ==================
def handle_visit_flow(from_number: str, user_message: str, phone: str) -> bool:
    s = ensure_session(from_number)

    # Inicio explícito
    if s["stage"] in ("idle","choose_mode") and want_visit(user_message):
        # si no eligió modo, pídelo primero
        if not s["mode"]:
            s["stage"] = "choose_mode"
            enviar_texto(from_number, "¿Quieres COMPRAR o RENTAR?")
            return True
        s["stage"] = "ask_name"
        enviar_texto(from_number, "Excelente. Para agendar la visita, ¿me compartes tu nombre completo?")
        return True

    if s["stage"] == "ask_name":
        name = user_message.strip()
        if 2 <= len(name) <= 80:
            s["name"] = name
            s["stage"] = "ask_email"
            enviar_texto(from_number, "Gracias. ¿Cuál es tu correo electrónico?")
        else:
            enviar_texto(from_number, "Perfecto. Compárteme tu nombre completo para continuar.")
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
            enviar_texto(from_number, "Ese correo no parece válido. ¿Puedes escribirlo así: nombre@dominio.com?")
        return True

    if s["stage"] == "ask_when":
        s["when"] = user_message.strip()
        s["stage"] = "closed"
        s["ready_to_notify"] = True
        enviar_texto(from_number, "Excelente, un asesor se pondrá en contacto contigo para coordinar la visita.")
        # Propiedad según modo
        prop_name = PRODUCTOS[s["mode"]]["nombre"] if s["mode"] in PRODUCTOS else "propiedad"
        on_lead_ready(s["name"], s["email"], phone, prop_name, s["when"])
        return True

    if s["stage"] == "closed":
        return True

    return False

# ================== FLASK ==================
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    phone = extract_phone(from_number)
    s = ensure_session(from_number)

    logger.info(f"📩 {from_number}: {user_message}")

    # 0) Saludo: siempre responde y resetea sesión
    if is_greeting(user_message):
        SESSIONS[from_number] = {"stage":"idle","mode":None,"name":None,"email":None,"when":None,"ready_to_notify":False}
        enviar_texto(from_number, "¡Hola! ¿Cómo puedo ayudarte hoy? ¿Buscas información de financiamiento o informes de propiedades?")
        return "OK", 200

    # 0.5) Piden informes/propiedades -> pedir COMPRAR o RENTAR
    if want_listings(user_message):
        s["stage"] = "choose_mode"
        enviar_texto(from_number, "Claro. ¿Te interesa COMPRAR o RENTAR?")
        return "OK", 200

    # 0.6) Responden modo explícito
    detected_mode = parse_mode(user_message)
    if s["stage"] in ("idle","choose_mode") and detected_mode:
    s["mode"] = detected_mode
    s["stage"] = "idle"

    if detected_mode == "venta":
        msg = ("¡Con gusto! Te comparto la opción disponible: un edificio en Puerto Escondido, Oaxaca, "
               "con 4 pisos y 8 departamentos. El precio es de 800,000 USD.")
    else:  # renta
        msg = ("¡Perfecto! Tenemos disponible un Pent House en la zona Tec con 2 habitaciones, "
               "2 baños completos, terraza privada, sala y comedor.")

    enviar_texto(from_number, msg)
    sleep(0.3)
    enviar_texto(from_number, "¿Quieres ver una foto o prefieres agendar una visita?")
    return "OK", 200

    # 1) Fotos (según modo)
    if want_photos(user_message):
        mode = s["mode"] or "renta"  # si no eligió, muestra renta por defecto
        prod = PRODUCTOS.get(mode)
        if prod and prod["imagenes"]:
            caption = "¿Te gustaría agendar una visita?"
            enviar_imagen(from_number, caption, prod["imagenes"][0])
        else:
            enviar_texto(from_number, f"{prod['descripcion'] if prod else 'Propiedad'}\n\nPor ahora sin imagen. ¿Agendamos visita?")
        return "OK", 200

    # 2) Flujo de agenda (nombre → email → horario → cierre + email)
    if handle_visit_flow(from_number, user_message, phone):
        return "OK", 200

    # 3) IA por defecto
    respuesta_texto = get_ai_reply(user_message)
    enviar_texto(from_number, respuesta_texto)
    return "OK", 200

# ===== Status callback para delivery de Twilio =====
@app.route("/twilio-status", methods=["POST"])
def twilio_status():
    logger.info(f"📬 Status callback: {dict(request.form)}")
    return "OK", 200

# ===== Test media canónica =====
@app.route("/test-media", methods=["POST"])
def test_media():
    to = request.form.get("From") or request.values.get("to")
    if not to: return "Falta 'From' o 'to'", 400
    url = "https://demo.twilio.com/owl.png"
    kwargs = dict(from_=TWILIO_WHATSAPP_NUMBER, to=to, body="Prueba media", media_url=[url])
    if STATUS_CALLBACK_URL: kwargs["status_callback"] = STATUS_CALLBACK_URL
    msg = twilio_client.messages.create(**kwargs)
    logger.info(f"🧪 Test media SID={msg.sid}")
    return "OK", 200

if __name__ == "__main__":
    logger.info("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
