import os, re
from time import sleep
from dotenv import load_dotenv
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import openai

# ================= ENV =================
load_dotenv()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")  # 'whatsapp:+1415...'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ================= PROMPT (refinado) =================
PROMPT = """
Eres un asistente inmobiliario digital de COINSA (SOFOM ENR, NL). Sé claro, cordial y breve (máx. 50 palabras).
Actualmente tenemos disponible un Pent House en la zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.
Objetivos:
1) Presenta esta propiedad como oportunidad destacada.
2) Si piden detalles, ofrece info adicional o fotos.
3) Motiva a agendar visita: pide nombre y correo; recuerda que el teléfono es el de este chat.
4) Mantén un tono confiable y profesional.
Contacto: 812 612 3414 · info@fcoinsa.com.mx
"""

# ================= PRODUCTO =================
PRODUCTO = {
    "nombre": "pent house zona tec",
    "descripcion": "Pent House en zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.",
    "imagenes": [
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/fachada_gv7dql.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/comedor_pjbxyq.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644419/habitacion_f6xchz.jpg"
    ]
}

# ================= SESIONES (RAM; usa Redis en prod) =================
# stage: idle | ask_name | ask_email | ask_when | closed
SESSIONS = {}  # { from: {stage,name,email,ready_to_notify}}

# ================= HELPERS =================
def get_ai_reply(user_message: str) -> str:
    try:
        r = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role":"system","content":PROMPT},
                      {"role":"user","content":user_message}],
            temperature=0.3,
            max_tokens=180
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ OpenAI: {e}")
        return "Ocurrió un error al procesar tu mensaje."

def enviar_texto(to_number: str, body: str):
    try:
        msg = twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, to=to_number, body=body)
        print(f"✅ Texto SID={msg.sid}")
    except Exception as e:
        print(f"❌ Twilio texto: {e}")

def enviar_texto_con_imagenes_album(to_number: str, body: str, media_urls):
    """
    Intenta enviar varias imágenes en UN mensaje (media_url=[...]).
    Si falla o solo aparece una, hace fallback y manda las restantes en mensajes separados.
    """
    if not isinstance(media_urls, list):
        media_urls = [media_urls]
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body,
            media_url=media_urls
        )
        print(f"✅ Álbum único SID={msg.sid} (n={len(media_urls)})")
        return
    except Exception as e:
        print(f"⚠️ Álbum único falló; envío secuencial: {e}")
    # Fallback
    try:
        first = media_urls[0]
        msg1 = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER, to=to_number, body=body, media_url=[first]
        )
        print(f"✅ Primera imagen SID={msg1.sid}")
        sleep(0.6)
        for u in media_urls[1:]:
            msgn = twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER, to=to_number, media_url=[u]
            )
            print(f"✅ Imagen extra SID={msgn.sid}")
            sleep(0.6)
    except Exception as e2:
        print(f"❌ Fallback álbum falló: {e2}")

def extract_phone(whatsapp_from: str) -> str:
    return whatsapp_from.replace("whatsapp:", "") if whatsapp_from else ""

def looks_like_email(text: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))

def want_photos(text: str) -> bool:
    t = text.lower()
    triggers = ["foto", "fotos", "imagen", "imágenes", "imagenes", "ver producto", "ver fotos", "mándame", "mandame"]
    return any(k in t for k in triggers)

def want_visit(text: str) -> bool:
    t = text.lower()
    triggers = ["agendar", "agenda", "visita", "cita", "tour", "recorrido", "verlo", "ver la propiedad", "quiero ver"]
    return any(k in t for k in triggers)

def is_greeting(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(x) for x in ["hola", "buenas", "buen día", "buen dia", "hey", "holi"]) or t in {"hi","hello","saludos"}

# Hook: aquí conectarás SendGrid después
def on_lead_ready(nombre: str, email: str, phone: str):
    print(f"🔔 Lead listo: {nombre} | {email} | {phone}  (aquí mandarás SendGrid)")

def ensure_session(num: str):
    return SESSIONS.setdefault(num, {"stage":"idle","name":None,"email":None,"ready_to_notify":False})

# ================= STATE MACHINE DE AGENDA =================
def handle_visit_flow(from_number: str, user_message: str, phone: str) -> bool:
    s = ensure_session(from_number)

    # inicio explícito
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
        # Cierra flujo y dispara trigger
        s["stage"] = "closed"
        s["ready_to_notify"] = True
        enviar_texto(from_number, "Excelente, un asesor se pondrá en contacto contigo para coordinar la visita.")
        on_lead_ready(s["name"], s["email"], phone)
        return True

    if s["stage"] == "closed":
        return True

    return False

# ================= FLASK =================
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    phone = extract_phone(from_number)

    print(f"📩 {from_number}: {user_message}")

    # 0) Saludo inicial -> siempre 2 mensajes seguidos
    if is_greeting(user_message) and SESSIONS.get(from_number, {}).get("stage", "idle") == "idle":
        enviar_texto(from_number, PRODUCTO["descripcion"])
        # pequeño respiro para orden de llegada (opcional)
        sleep(0.4)
        enviar_texto(from_number, "¿Quieres ver fotos o prefieres agendar una visita?")
        return "OK", 200

    # 1) Fotos del producto
    if want_photos(user_message):
        caption = f"{PRODUCTO['descripcion']}\n\nContacto: 812 612 3414 · info@fcoinsa.com.mx"
        enviar_texto_con_imagenes_album(from_number, caption, PRODUCTO["imagenes"])
        return "OK", 200

    # 2) Flujo de agenda (nombre → email → disponibilidad → cierre + trigger)
    if handle_visit_flow(from_number, user_message, phone):
        return "OK", 200

    # 3) Respuesta normal con IA
    respuesta_texto = get_ai_reply(user_message)
    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return str(resp), 200

if __name__ == "__main__":
    print("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
