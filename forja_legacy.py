#!/usr/bin/env python3
"""Forja - AI-powered project builder."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

FORJA_DIR = Path(".forja")
LOGS_DIR = FORJA_DIR / "logs"

HOP_PROMPT = (
    "Eres el Head of Product de Forja. Lee este PRD y descompón en 2-3 épicas independientes. "
    "Responde SOLO con JSON válido, sin markdown, sin explicaciones. Formato:\n"
    "{\n"
    '  "project_name": "nombre",\n'
    '  "epics": [\n'
    "    {\n"
    '      "id": "epic-1",\n'
    '      "name": "nombre corto",\n'
    '      "description": "qué debe construir esta épica",\n'
    '      "files": ["lista de archivos que debe crear"],\n'
    '      "acceptance_criteria": ["lista de criterios para considerar la épica completa"]\n'
    "    }\n"
    "  ]\n"
    "}"
)

CELL_PROMPT_TEMPLATE = (
    "Eres un ingeniero de software senior. Tu única tarea es implementar esta épica:\n"
    "Nombre: {name}\n"
    "Descripción: {description}\n"
    "Archivos a crear: {files}\n"
    "Criterios de aceptación: {acceptance_criteria}\n\n"
    "Trabaja en el directorio actual. Crea todos los archivos necesarios.\n"
    "Cuando termines, haz git add y git commit con mensaje descriptivo.\n"
    "No pidas confirmación. Solo ejecuta."
)


def log(msg):
    """Print timestamped message to terminal."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def save_log(name, content):
    """Save content to a log file in .forja/logs/."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"{ts}_{name}.log"
    path.write_text(content, encoding="utf-8")
    return path


def run_claude(prompt, log_name):
    """Run claude -p with the given prompt. Returns stdout as string."""
    log(f"Lanzando claude para: {log_name}")
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text", prompt],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    if result.returncode != 0:
        error_msg = result.stderr or "unknown error"
        save_log(f"{log_name}_error", f"STDERR:\n{error_msg}\n\nSTDOUT:\n{output}")
        log(f"ERROR en {log_name}: {error_msg[:200]}")
        sys.exit(1)
    save_log(log_name, output)
    return output


def parse_epics(raw):
    """Extract and parse JSON from claude output."""
    text = raw.strip()
    # Handle markdown code blocks
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        inner = text[start:end]
        # Remove opening ```json or ```
        first_newline = inner.find("\n")
        text = inner[first_newline + 1:] if first_newline != -1 else inner[3:]
    # Find JSON object boundaries
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start == -1 or brace_end == -1:
        log("ERROR: No se encontró JSON válido en la respuesta del HoP")
        save_log("hop_parse_error", raw)
        sys.exit(1)
    json_str = text[brace_start:brace_end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log(f"ERROR parseando JSON: {e}")
        save_log("hop_parse_error", f"Error: {e}\n\nRaw:\n{json_str}")
        sys.exit(1)


def run_build_tests():
    """Run build/tests if common config files exist."""
    checks = [
        (Path("package.json"), ["npm", "test"]),
        (Path("Makefile"), ["make", "test"]),
        (Path("pytest.ini"), ["pytest"]),
        (Path("pyproject.toml"), ["pytest"]),
        (Path("setup.py"), ["pytest"]),
    ]
    for config, cmd in checks:
        if config.exists():
            log(f"Detectado {config.name}, corriendo: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            save_log("tests", f"CMD: {' '.join(cmd)}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")
            if result.returncode == 0:
                log("Tests pasaron OK")
            else:
                log(f"Tests fallaron (exit {result.returncode})")
            return
    log("No se detectó framework de tests. Saltando.")


def run(prd_path):
    """Main orchestration: read PRD -> plan epics -> execute each -> test."""
    prd_file = Path(prd_path)
    if not prd_file.exists():
        log(f"ERROR: No existe {prd_path}")
        sys.exit(1)

    prd_content = prd_file.read_text(encoding="utf-8")
    if not prd_content.strip():
        log(f"ERROR: {prd_path} está vacío")
        sys.exit(1)

    log(f"PRD cargado: {prd_path} ({len(prd_content)} chars)")

    # Step 1: HoP decomposes PRD into epics
    log("=== FASE 1: Head of Product descompone épicas ===")
    hop_input = f"{HOP_PROMPT}\n\n---\n\nPRD:\n{prd_content}"
    raw_plan = run_claude(hop_input, "hop_plan")
    plan = parse_epics(raw_plan)

    project_name = plan.get("project_name", "unknown")
    epics = plan.get("epics", [])
    log(f"Proyecto: {project_name} | Épicas: {len(epics)}")

    save_log("hop_plan_parsed", json.dumps(plan, indent=2, ensure_ascii=False))

    # Step 2: Execute each epic with a Cell agent
    log("=== FASE 2: Ingenieros ejecutan épicas ===")
    results = []
    for epic in epics:
        epic_id = epic.get("id", "unknown")
        epic_name = epic.get("name", "unnamed")
        log(f"--- Épica {epic_id}: {epic_name} ---")

        cell_prompt = CELL_PROMPT_TEMPLATE.format(
            name=epic_name,
            description=epic.get("description", ""),
            files=", ".join(epic.get("files", [])),
            acceptance_criteria="\n".join(f"- {c}" for c in epic.get("acceptance_criteria", [])),
        )
        output = run_claude(cell_prompt, f"cell_{epic_id}")
        results.append({"epic": epic_id, "name": epic_name, "output_length": len(output)})
        log(f"Épica {epic_id} completada ({len(output)} chars de output)")

    # Step 3: Run tests if applicable
    log("=== FASE 3: Build / Tests ===")
    run_build_tests()

    # Step 4: Summary
    log("=== RESUMEN ===")
    log(f"Proyecto: {project_name}")
    for r in results:
        log(f"  {r['epic']}: {r['name']} ({r['output_length']} chars)")
    log("Forja completado.")


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        print("Uso: python forja.py run <ruta-al-prd.md>")
        sys.exit(1)
    run(sys.argv[2])


if __name__ == "__main__":
    main()
