# Ejemplos de uso de la API

Suponiendo la API en `http://localhost:8000`.

1) Comprobación de estado

```bash
curl http://localhost:8000/health
```

Respuesta esperada:

```json
{"status": "ok"}
```

2) Login (obtener JWT)

El endpoint de autenticación usa `application/x-www-form-urlencoded` (OAuth2).

```bash
curl -X POST \
  -d "username=mi_uvus&password=mi_contraseña" \
  http://localhost:8000/auth/login
```

Respuesta (JSON):

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "rol": "revisor",
  "nombre": "Nombre Usuario"
}
```

3) Listar clips

```bash
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8000/clips
```

4) Registrar un clip (JSON)

```bash
curl -X POST http://localhost:8000/clips \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"nombre_archivo":"audio123.wav","transcripcion":"Texto de ejemplo","hablante_id":1}'
```

5) Obtener clips pendientes de transcripción

```bash
curl -H "Authorization: Bearer <TOKEN>" http://localhost:8000/transcripciones/pendientes
```

6) Actualizar transcripción (revisor)

```bash
curl -X PUT http://localhost:8000/clips/123 \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"transcripcion":"Texto corregido"}'
```

Notas:
- Reemplaza `<TOKEN>` por el `access_token` devuelto por `/auth/login`.
- Si añades endpoints nuevos, documenta ejemplo equivalente aquí.
