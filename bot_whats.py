import os, re, logging
from logging.handlers import RotatingFileHandler
from time import sleep
from dotenv import load_dotenv
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import openai

# ============== ENV ==============
load_dotenv()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")   # ej: 'whatsapp:+14155238886'
STATUS_CALLBACK_URL = os.environ.get("STATUS_CALLBACK_URL")         # ej: 'https://tuapp.railway.app/twilio-status'

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ============== LOGGING ==============
logger = logging.getLogger("coinsa-bot")
logger.setLevel(logging.INFO)
# Consola
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)
# Archivo (en Railway se guarda en el contenedor; útil para inspección rápida)
file_handler = RotatingFileHandler("twilio_status.log", maxBytes=1_000_000, backupCount=3)
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

# ============== PROMPT (refinado) ==============
PROMPT = """
Eres un asistente inmobiliario digital de COINSA (SOFOM ENR, NL). Sé claro, amable y breve (máx. 50 palabras).

Hecho importante: actualmente SOLO hay UNA propiedad disponible. No digas ni insinúes que hay varias.

Flujo:
- Si saludan: saluda y pregunta cómo ayudar (no presentes aún la propiedad).
- Si preguntan por propiedades disponibles, lista o inventario: responde directamente con la única opción disponible (Pent House zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor) y ofrece enviar fotos o agendar visita.
- Si piden fotos: envía y pregunta si desean agendar.
- Para agendar: pide nombre y correo; el teléfono es el de este chat.
- Evita bloques de contacto salvo que lo pidan explícitamente.
"""

# ============== PRODUCTO ==============
PRODUCTO = {
    "nombre": "pent house zona tec",
    "descripcion": "Pent House en zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.",
    "imagenes": [

        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/comedor_pjbxyq.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644419/habitacion_f6xchz.jpg"
    ]
}

# ============== SESIONES (RAM; usa Redis/DB en prod) ==============
# stage: idle | ask_name | ask_email | ask_when | closed
SESSIONS = {}  # { from_number: {stage, name, email, when, ready_to_notify}}

def ensure_session(num: str):
    return SESSIONS.setdefault(num, {
        "stage":"idle",
        "name":None,
        "email":None,
        "when":None,
        "ready_to_notify":False
    })

# ============== HELPERS ==============
def optimize(url: str) -> str:
    # Fuerza JPG, compresión automática y ancho razonable para WhatsApp
    return url.replace("/upload/", "/upload/f_jpg,q_auto,w_1280/")

def get_ai_reply(user_message: str) -> str:
    try:
        r = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role":"system","content":PROMPT},
                {"role":"user","content":user_message}
            ],
            temperature=0.3,
            max_tokens=180
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.exception(f"OpenAI error: {e}")
        return "Ocurrió un error al procesar tu mensaje."

def enviar_texto(to_number: str, body: str):
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body
        )
        logger.info(f"✅ Texto SID={msg.sid}")
    except Exception as e:
        logger.exception(f"Twilio texto error: {e}")

def enviar_texto_con_imagen_una(to_number: str, body: str, url: str):
    # Envía UNA imagen optimizada + caption
    try:
        url_opt = optimize(url)
        logger.info(f"🖼️ Enviando imagen: {url_opt}")
        kwargs = dict(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body,
            media_url=[url_opt]
        )
        if STATUS_CALLBACK_URL:
            kwargs["status_callback"] = STATUS_CALLBACK_URL
        msg = twilio_client.messages.create(**kwargs)
        logger.info(f"✅ Texto+Imagen SID={msg.sid}")
    except Exception as e:
        logger.exception(f"Twilio media error: {e}")

def extract_phone(whatsapp_from: str) -> str:
    return whatsapp_from.replace("whatsapp:", "") if whatsapp_from else ""

def looks_like_email(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))

def want_photos(text: str) -> bool:
    t = text.lower()
    triggers = [
        "foto","fotos","imagen","imágenes","imagenes",
        "ver producto","ver fotos","a ver las fotos",
        "quiero ver las fotos","enséñame","enseñame","mándame","mandame"
    ]
    hit = any(k in t for k in triggers)
    logger.info(f"🔎 want_photos={hit} | '{t}'")
    return hit

def want_visit(text: str) -> bool:
    t = text.lower()
    triggers = ["agendar","agenda","visita","cita","tour","recorrido","verlo","ver la propiedad","quiero ver"]
    return any(k in t for k in triggers)

def want_listings(text: str) -> bool:
    t = text.lower().strip()
    triggers = [
        "qué propiedades tienes","que propiedades tienes","propiedades disponibles",
        "qué tienes","que tienes","qué propiedades","inventario","lista de propiedades",
        "que inmuebles tienes","inmuebles disponibles"
    ]
    return any(k in t for k in triggers)

def is_greeting(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(x) for x in ["hola","buenas","buen día","buen dia","hey","holi"]) or t in {"hi","hello","saludos"}

# Hook para notificar al cerrar lead (integra SendGrid aquí)
def on_lead_ready(nombre: str, email: str, phone: str, propiedad: str, when_str: str | None):
    logger.info(f"🔔 Lead listo: {nombre} | {email} | {phone} | {propiedad} | {when_str}")
    # TODO: Integrar SendGrid aquí (enviar_correo_lead(nombre, email, phone, propiedad, when_str))

# ============== STATE MACHINE: agendar visita ==============
def handle_visit_flow(from_number: str, user_message: str, phone: str) -> bool:
    s = ensure_session(from_number)

    if s["stage"] == "idle" and want_visit(user_message):
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
        # Guarda literal la preferencia de horario y cierra
        when_str = user_message.strip()
        s["when"] = when_str
        s["stage"] = "closed"
        s["ready_to_notify"] = True

        enviar_texto(from_number, "Excelente, un asesor se pondrá en contacto contigo para coordinar la visita.")
        on_lead_ready(s["name"], s["email"], phone, PRODUCTO["nombre"], s["when"])
        return True

    if s["stage"] == "closed":
        return True

    return False

# ============== FLASK ==============
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    phone = extract_phone(from_number)

    logger.info(f"📩 {from_number}: {user_message}")

    # 0) Saludo inicial -> solo pregunta cómo ayudar
    if is_greeting(user_message) and SESSIONS.get(from_number, {}).get("stage", "idle") == "idle":
        enviar_texto(from_number, "¡Hola! ¿Cómo puedo ayudarte hoy? ¿Buscas información de financiamiento o quieres conocer la propiedad disponible?")
        return "OK", 200

    # 0.5) Preguntan por inventario -> responde con la única propiedad y CTA
    if want_listings(user_message):
        enviar_texto(from_number, PRODUCTO["descripcion"])
        sleep(0.3)
        enviar_texto(from_number, "¿Quieres ver fotos o prefieres agendar una visita?")
        return "OK", 200

    # 1) Fotos del producto (UNA imagen por ahora)
    if want_photos(user_message):
        caption = f"{PRODUCTO['descripcion']}\n\n¿Te gustaría agendar una visita?"
        primera_url = PRODUCTO["imagenes"][0]
        enviar_texto_con_imagen_una(from_number, caption, primera_url)
        return "OK", 200

    # 2) Flujo de agenda (nombre → email → disponibilidad → cierre + trigger)
    if handle_visit_flow(from_number, user_message, phone):
        return "OK", 200

    # 3) Respuesta normal con IA (texto)
    respuesta_texto = get_ai_reply(user_message)
    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return str(resp), 200

# ===== Status callback de Twilio (ver en Railway Logs y archivo) =====
@app.route("/twilio-status", methods=["POST"])
def twilio_status():
    payload = dict(request.form)
    logger.info(f"📬 Status callback: {payload}")
    return "OK", 200

# ===== Endpoint de prueba para media canónica =====
@app.route("/test-media", methods=["POST"])
def test_media():
    # Twilio enviará 'From' cuando esto se use como webhook; si no, puedes pasar 'to' por query/form en pruebas manuales
    to = request.form.get("From") or request.values.get("to")
    if not to:
        return "Falta 'From' (WhatsApp) o 'to' para pruebas", 400

    url = "https://demo.twilio.com/owl.png"
    logger.info(f"🧪 Test media -> {url} to {to}")
    kwargs = dict(
        from_=TWILIO_WHATSAPP_NUMBER,
        to=to,
        body="Prueba media",
        media_url=[url]
    )
    if STATUS_CALLBACK_URL:
        kwargs["status_callback"] = STATUS_CALLBACK_URL
    msg = twilio_client.messages.create(**kwargs)
    logger.info(f"🧪 Test media SID={msg.sid}")
    return "OK", 200

if __name__ == "__main__":
    logger.info("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
