# Cómo obtener una API key válida para Gemini

Si recibes **"API key not valid"**, sigue estos pasos:

## 1. Obtener la clave
- Entra a: **https://aistudio.google.com/apikey**
- Inicia sesión con tu cuenta de Google
- Haz clic en **"Create API key"**
- Elige un proyecto de Google Cloud (o crea uno nuevo)
- Copia la clave (empieza con `AIza...`)

## 2. Verificar restricciones
En [Google Cloud Console](https://console.cloud.google.com/apis/credentials):
- Entra a tu proyecto
- Ve a "APIs y servicios" → "Credenciales"
- Busca tu API key y comprueba:
  - **Restricciones de API**: Si hay restricciones, debe incluir "Generative Language API"
  - **Restricciones de aplicación**: Si hay restricciones de IP o referrer, pueden bloquear tu app local
AIzaSyAvD2cH0vvPyqm-F3gMfEMakoHbkqW4Nz4
## 3. Habilitar la API
- En [APIs habilitadas](https://console.cloud.google.com/apis/library), busca **"Generative Language API"**
- Actívala si no lo está

## 4. Copiar correctamente en .env
```
GOOGLE_API_KEY=AIzaSy...tu-clave-completa...
```
- Sin comillas
- Sin espacios antes o después del `=`
- Una sola línea, sin saltos de línea
- Guarda el archivo

## 5. Probar
```powershell
python test_converter.py tu-archivo.pdf -g
```
