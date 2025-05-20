import openai
import requests
import cloudinary
import cloudinary.uploader
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import os
from dotenv import load_dotenv

# ===== CARGAR VARIABLES DE ENTORNO =====
load_dotenv()

# ===== CONFIGURACIÓN DE CLAVES =====
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVEN_API_KEY = os.environ.get("ELEVEN_API_KEY")
VOICE_ID = os.environ.get("VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")

# ===== CONFIGURACIÓN CLOUDINARY =====
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET")
)

# ===== CLIENTES =====
openai.api_key = OPENAI_API_KEY
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ===== PROMPT DE LA IA =====
PROMPT = """
Eres el bot de WhatsApp de Racel, experto en responder dudas sobre nuestros productos retail. 
Olorex es nuestra marca líder en cuidado de pies y cuerpo, con más de 40 productos activos, reconocida por su eficacia para mantener los pies frescos y sin mal olor. 
Fly Out es nuestro repelente natural, formulado con extractos y aceites esenciales, ideal para actividades al aire libre y respetuoso con el medio ambiente, con más de 10 presentaciones. 
Pet Master es el desodorizante para arenero de gato lanzado en 2021, con aroma refrescante, que neutraliza los malos olores y mejora la convivencia entre mascotas y dueños. 
Mentolex ambienta y refresca espacios con su fragancia a base de menta, eucalipto y aceites esenciales, disponible en spray y ungüento. 
Dolox es un aliado natural para el bienestar muscular, que alivia tensiones antes y después de la actividad física. 
Olormax Talco Desodorante para Pies y Cuerpo refresca y mantiene la sensación de limpieza en todo el cuerpo de manera duradera. 
Responde de forma profesional, breve y precisa, limitándote únicamente a lo que pregunte el usuario.
"""

# ===== FLASK APP =====
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_bot():
    user_message = request.form.get("Body")
    from_number = request.form.get("From")

    print(f"✅ Recibido mensaje de {from_number}: {user_message}")

    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": user_message}
            ]
        )
        respuesta_texto = response.choices[0].message.content
        print(f"✅ Respuesta de OpenAI: {respuesta_texto}")

    except Exception as e:
        print(f"❌ Error en OpenAI: {e}")
        respuesta_texto = "Lo siento, ocurrió un error al procesar tu mensaje."

    audio_file = elevenlabs_tts(respuesta_texto)

    if audio_file:
        audio_url = subir_a_cloudinary(audio_file)
    else:
        audio_url = None

    if audio_url:
        try:
            message = twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER,
                to=from_number,
                media_url=[audio_url]
            )
            print(f"✅ Audio enviado a {from_number}: {audio_url}")
        except Exception as e:
            print(f"❌ Error enviando audio: {e}")
    else:
        print("⚠️ No se pudo generar el audio. Enviando mensaje de texto.")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return str(resp)

    return "OK", 200

def elevenlabs_tts(texto):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    print("🎙️ Generando audio en Eleven Labs...")
    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 200:
        file_name = "respuesta.mp3"
        with open(file_name, "wb") as f:
            f.write(response.content)

        print("✅ Audio guardado como respuesta.mp3")
        return file_name
    else:
        print(f"❌ Error en Eleven Labs: {response.status_code} - {response.text}")
        return None

def subir_a_cloudinary(file_path):
    print("☁️ Subiendo audio a Cloudinary...")
    try:
        result = cloudinary.uploader.upload(file_path, resource_type="video")
        audio_url = result.get("secure_url")
        print(f"✅ Audio subido a Cloudinary: {audio_url}")

        os.remove(file_path)
        print("🧹 Archivo local eliminado después de subir.")
        return audio_url
    except Exception as e:
        print(f"❌ Error al subir a Cloudinary: {e}")
        return None

if __name__ == "__main__":
    print("🚀 Bot corriendo en http://localhost:5001/whatsapp")
    PORT = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=PORT, debug=True)
