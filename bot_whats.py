import os
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

# ===== PROMPT =====
PROMPT = """
Eres un bot asistente inmobiliario, socio financiero digital de Crédito Operativo Integral, SA de CV, SOFOM ENR, con más de 10 años de experiencia en el ramo financiero, radicados en Nuevo León. 
Proporciona información concisa y precisa sobre nuestros productos crediticios para comprar oportunidades inmobiliarias.
Tasas competitivas: la tasa es el costo del financiamiento durante el plazo pactado y utilizamos tasas sobre saldos, de modo que al abonar capital, los intereses disminuyen.
Créditos flexibles: ofrecemos esquemas de pago amortizable (capital más interés mes a mes) o pago flexible (línea de crédito con intereses según capital dispuesto).
Pagos fijos o flexibles: adapta el esquema a los flujos; por ejemplo, pago amortizable para maquinaria o línea de crédito revolvente para capital de trabajo.
Tiempo de respuesta rápido: garantizamos respuestas ágiles para evitar urgencias y costos innecesarios.

Ejemplos de productos: Pent House en zona Tec. 
Características: 2 habitaciones, 2 baños completos, terraza privada, sala, comedor, 
Contacto: 812 612 3414, info@fcoinsa.com.mx. 
Actúa con profesionalismo y brevedad en cada interacción, utilizando máximo 45 palabras y limitándote a responder lo que pregunte el usuario.
"""

# ===== PRODUCTO (1 producto, 3 imágenes) =====
PRODUCTO = {
    "nombre": "pent house zona tec",
    "descripcion": "Pent House en zona Tec: 2 habitaciones, 2 baños completos, terraza privada, sala y comedor.",
    "imagenes": [
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/fachada_gv7dql.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644414/comedor_pjbxyq.jpg",
        "https://res.cloudinary.com/dafozmwvq/image/upload/v1757644419/habitacion_f6xchz.jpg"
    ]
}

# ===== HELPERS =====
def get_ai_reply(user_message: str) -> str:
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=180
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Error OpenAI: {e}")
        return "Lo siento, ocurrió un error al procesar tu mensaje."

def enviar_texto(to_number: str, body: str):
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body
        )
        print(f"✅ Texto enviado: {msg.sid}")
    except Exception as e:
        print(f"❌ Error enviando texto: {e}")

def enviar_texto_con_imagenes(to_number: str, body: str, media_urls):
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body,
            media_url=media_urls
        )
        print(f"✅ Texto+Imágenes enviado: {msg.sid}")
    except Exception as e:
        print(f"❌ Error enviando texto+imágenes: {e}")

# ===== FLASK =====
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body", "")
    from_number = request.form.get("From", "")

    print(f"📩 Recibido de {from_number}: {user_message}")

    # Si usuario pide ver fotos del producto
    if any(k in user_message.lower() for k in ["foto", "imagen", "fotos", "imágenes", "ver producto", "ver fotos"]):
        caption = f"{PRODUCTO['descripcion']}\n\nContacto: 812 612 3414 · info@fcoinsa.com.mx"
        enviar_texto_con_imagenes(from_number, caption, PRODUCTO["imagenes"])
        return "OK", 200

    # Respuesta normal con IA
    respuesta_texto = get_ai_reply(user_message)
    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return str(resp), 200

if __name__ == "__main__":
    print("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
