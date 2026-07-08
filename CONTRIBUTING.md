# Contributing

Gracias por contribuir. Estas pautas ayudan a mantener el repositorio limpio y fácil
de mantener.

1. Flujo de trabajo
- Trabaja en ramas por feature: `feature/<descripcion>` o `fix/<descripcion>`.
- Mantén commits pequeños y atómicos con mensajes descriptivos.

2. Código y estilo
- Sigue la convención de estilo de Python del proyecto. Se recomiendan `black` y `ruff`.
- Añade tipado donde aporte claridad y estabilidad (`mypy` opcional).

3. Dependencias
- Si añades una dependencia, actualiza `requirements.txt` y explica la razón en el PR.
- Evita incluir dependencias innecesarias.

4. Tests
- Añade tests unitarios para cambios en lógica crítica. Coloca tests en `tests/`.
- Ejecuta `pytest` antes de abrir el PR.

5. Seguridad y datos
- No incluyas credenciales en el código ni en commits.
- Usa `.env` y `config_local.py` (añadir a `.gitignore`) para configuraciones locales.

6. Revisión
- Abre Pull Request hacia `main` (o rama de integración) con descripción de los cambios.
- Añade capturas o ejemplos de ejecución si el cambio es visible (UI/outputs/api).

7. Checklist mínimo para PRs
- Código formateado (`black`).
- Tests relevantes añadidos/actualizados.
- `requirements.txt` actualizado si aplica.
- Documentación actualizada (`README.md`, `API_EXAMPLES.md`).

Si quieres que añada configuraciones de pre-commit o plantillas automáticas, puedo
generarlas en este repo.
