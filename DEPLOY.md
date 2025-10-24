# Gu�a de Deployment - Music Player

Esta gu�a te muestra c�mo desplegar el proyecto en **Render** (backend) y **GitHub Pages** (frontend).

## =� Requisitos Previos

- Cuenta en [GitHub](https://github.com)
- Cuenta en [Render](https://render.com)
- Git instalado localmente

## =� Parte 1: Desplegar Backend en Render

### 1. Preparar el repositorio

```bash
# Crear repositorio en GitHub
git init
git add .
git commit -m "Initial commit: Music Player con rooms"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/music-player.git
git push -u origin main
```

### 2. Configurar en Render

1. Ve a [Render Dashboard](https://dashboard.render.com/)
2. Click en "New +" � "Web Service"
3. Conecta tu repositorio de GitHub
4. Configura el servicio:

   **Configuraci�n:**
   - **Name**: `music-player-api` (o el nombre que prefieras)
   - **Region**: Elige la m�s cercana a ti
   - **Branch**: `main`
   - **Root Directory**: `music-app-fastapi` (o deja vac�o si est� en la ra�z)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

   **Plan**: Free (gratis)

5. Click en "Create Web Service"

### 3. Verificar deployment

- Render te dar� una URL como: `https://music-player-api.onrender.com`
- Espera a que termine el deploy (puede tardar 2-3 minutos)
- Visita: `https://music-player-api.onrender.com/api/tracks` para verificar que funcione

## < Parte 2: Desplegar Frontend en GitHub Pages

### 1. Modificar el frontend para producci�n

Edita `index.html` y busca la funci�n `proto()` y las conexiones WebSocket para que usen la URL de Render:

```javascript
// Cambiar esto:
const wsUrl = `${this.proto()}://${location.host}/ws/${roomPath}...`

// Por esto (usando tu URL de Render):
const API_URL = 'music-player-api.onrender.com';  // TU URL DE RENDER (sin https://)
const wsUrl = `wss://${API_URL}/ws/${roomPath}...`

// Y para las llamadas HTTP:
fetch('https://music-player-api.onrender.com/api/...')
```

### 2. Crear archivo de configuraci�n de GitHub Pages

Crea un archivo `docs/index.html` o usa el `index.html` actual:

```bash
# Opci�n 1: Usar carpeta docs
mkdir docs
cp index.html docs/

# Opci�n 2: Usar rama gh-pages (recomendado)
git checkout -b gh-pages
git add index.html
git commit -m "Deploy to GitHub Pages"
git push origin gh-pages
```

### 3. Activar GitHub Pages

1. Ve a tu repositorio en GitHub
2. Click en "Settings" � "Pages"
3. En "Source", selecciona:
   - **Branch**: `gh-pages` (o `main` si usaste docs/)
   - **Folder**: `/root` (o `/docs` si usaste carpeta docs)
4. Click en "Save"

### 4. Acceder a tu aplicaci�n

Tu app estar� disponible en:
- `https://TU_USUARIO.github.io/music-player/`

## � Configuraci�n Avanzada

### Variables de Entorno en Render

Si necesitas configurar variables de entorno:

1. En Render Dashboard � Tu servicio � "Environment"
2. Agregar variables:
   - `STREAMING_MODE=direct` (ya est� por defecto)
   - Otras variables si las necesitas

### CORS en el Backend

El backend ya est� configurado para aceptar cualquier origen. Si quieres restringirlo:

```python
# En main.py, busca:
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://TU_USUARIO.github.io"],  # Tu dominio de GitHub Pages
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## =' Soluci�n de Problemas

### El backend no inicia en Render

1. Revisa los logs en Render Dashboard
2. Verifica que `requirements.txt` est� actualizado
3. Aseg�rate de que el Start Command sea correcto

### WebSocket no conecta

1. Verifica que uses `wss://` (no `ws://`) para HTTPS
2. Confirma que la URL del backend sea correcta
3. Revisa la consola del navegador para errores

### GitHub Pages no muestra la app

1. Verifica que la rama y carpeta sean correctas en Settings � Pages
2. Espera 1-2 minutos para que se publique
3. Limpia cach� del navegador (Ctrl+Shift+R)

## =� Notas Importantes

- **Render Free Tier**: El servidor se duerme despu�s de 15 minutos de inactividad. La primera petici�n puede tardar 30-60 segundos en despertar.
- **WebSocket en Render**: Los WebSockets est�n soportados en el plan gratuito.
- **Persistencia**: Los datos (rooms, sesiones) se pierden al reiniciar en Render Free. Para persistencia, necesitas una base de datos externa.

## <� �Listo!

Tu aplicaci�n Music Player est� desplegada y funcionando en la nube!

**URLs finales:**
- Frontend: `https://TU_USUARIO.github.io/music-player/`
- Backend API: `https://music-player-api.onrender.com`
