import os, re
from dotenv import load_dotenv
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import openai

# ===== ENV =====
load_dotenv()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")  # ej: 'whatsapp:+14155238886'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ===== PROMPT (sesgo a agendar visita) =====
PROMPT = """
Eres un bot asistente inmobiliario de COINSA (SOFOM ENR, NL). Sé breve (máx. 45 palabras) y preciso.
Si el usuario muestra interés en visitar/recorrer, incentiva agendar visita y solicita nombre y correo electrónico.
Aclara que el teléfono será el de este chat. Responde solo a lo que pregunta.
Ejemplo de producto: Pent House en zona Tec con 2 habitaciones, 2 baños, terraza, sala y comedor.
Contacto: 812 612 3414 · info@fcoinsa.com.mx
"""

# ===== PRODUCTO: 3 imágenes =====
PRODUCTO = {
    "nombre": "pent house zona tec",
    "descripcion": "Pent House en zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.",
    "imagenes": [
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/fachada_gv7dql.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/comedor_pjbxyq.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644419/habitacion_f6xchz.jpg"
    ]
}

# ===== SESIONES (MVP: memoria en RAM; en prod usa Redis) =====
SESSIONS = {}  # { from_number: {"stage": "idle"|"ask_name"|"ask_email"|"done", "name": str|None, "email": str|None} }

# ===== HELPERS =====
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

def enviar_texto_con_imagenes(to_number: str, body: str, media_urls):
    # media_urls puede ser lista (hasta ~10)
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body,
            media_url=media_urls
        )
        print(f"✅ Texto+Imágenes SID={msg.sid}")
    except Exception as e:
        print(f"❌ Twilio media: {e}")

def extract_phone(whatsapp_from: str) -> str:
    # 'whatsapp:+5215512345678' -> '+5215512345678'
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

def next_step_profile(from_number: str, user_message: str, phone: str):
    """
    Pequeño state machine:
    - ask_name -> guarda nombre y pide email
    - ask_email -> valida y cierra
    """
    state = SESSIONS.setdefault(from_number, {"stage":"ask_name","name":None,"email":None})

    # Si venía en flujo, continúa
    if state["stage"] == "ask_name":
        # ¿mensaje contiene un posible nombre? guardamos todo el texto como nombre.
        name = user_message.strip()
        if 2 <= len(name) <= 80:
            state["name"] = name
            state["stage"] = "ask_email"
            enviar_texto(from_number, "Gracias. ¿Cuál es tu correo electrónico?")
            return True
        else:
            enviar_texto(from_number, "Perfecto. Para agendar, compárteme tu nombre completo.")
            return True

    if state["stage"] == "ask_email":
        if looks_like_email(user_message):
            state["email"] = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message).group(0)
            state["stage"] = "done"
            enviar_texto(
                from_number,
                f"Listo, {state['name']}. Te contacto al {phone} y {state['email']} para coordinar la visita. "
                "¿Tienes día y horario preferido?"
            )
            return True
        else:
            enviar_texto(from_number, "Ese correo no parece válido. ¿Puedes escribirlo así: nombre@dominio.com?")
            return True

    # Si está done pero el usuario insiste, reinicia a pedir email por si quiere cambiarlo
    if state["stage"] == "done":
        enviar_texto(from_number, "Si quieres actualizar tus datos, indícame correo nuevo o escribe 'agendar visita'.")
        return True

    return False

# ===== FLASK =====
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    phone = extract_phone(from_number)

    print(f"📩 {from_number}: {user_message}")

    # 1) Si pide fotos, mandamos las 3 en un solo mensaje
    if want_photos(user_message):
        caption = f"{PRODUCTO['descripcion']}\n\nContacto: 812 612 3414 · info@fcoinsa.com.mx"
        enviar_texto_con_imagenes(from_number, caption, PRODUCTO["imagenes"])
        return "OK", 200

    # 2) Si quiere visita (o ya está en flujo), corremos el perfilado
    if want_visit(user_message) or SESSIONS.get(from_number, {}).get("stage") in {"ask_name","ask_email","done"}:
        # Si recién pidió visita y no hay estado, comenzamos pidiendo nombre
        st = SESSIONS.get(from_number)
        if not st:
            SESSIONS[from_number] = {"stage":"ask_name","name":None,"email":None}
            enviar_texto(from_number, "Excelente. Para agendar la visita, ¿me compartes tu nombre completo?")
            return "OK", 200
        # Continuar flujo
        handled = next_step_profile(from_number, user_message, phone)
        if handled:
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
