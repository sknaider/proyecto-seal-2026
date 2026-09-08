"""
Verifica que el modelo Ollama por defecto es qwen2.5:3b en config y aux_llm.
William autorizó el cambio en DM 153680 ("sigamos tu recomendación qwen2.5:3b").
"""
import importlib, pathlib, ast, sys

REPO = pathlib.Path(__file__).parents[2]

# ---------------------------------------------------------------------------
# qa_positive — los defaults dicen 3b
# ---------------------------------------------------------------------------

def test_qa_positive_config_default_es_3b():
    """SealConfig.ollama_model default debe ser qwen2.5:3b."""
    src = (REPO / "memory/config.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "ollama_model":
                assert isinstance(node.value, ast.Constant), "ollama_model must be a string literal"
                assert node.value.value == "qwen2.5:3b", (
                    f"config.py default es {node.value.value!r}, esperaba 'qwen2.5:3b'"
                )
                return
    raise AssertionError("ollama_model no encontrado en memory/config.py")


def test_qa_positive_aux_llm_default_es_3b():
    """OLLAMA_MODEL fallback en aux_llm.py debe ser qwen2.5:3b."""
    src = (REPO / "memory/aux_llm.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "OLLAMA_MODEL":
                    # os.environ.get("AUX_LLM_OLLAMA_MODEL", "qwen2.5:3b")
                    if isinstance(node.value, ast.Call):
                        args = node.value.args
                        if len(args) >= 2 and isinstance(args[1], ast.Constant):
                            assert args[1].value == "qwen2.5:3b", (
                                f"aux_llm.py OLLAMA_MODEL fallback es {args[1].value!r}, esperaba 'qwen2.5:3b'"
                            )
                            return
    raise AssertionError("OLLAMA_MODEL no encontrado o sin fallback literal en memory/aux_llm.py")


# ---------------------------------------------------------------------------
# qa_negative — NO dicen 7b
# ---------------------------------------------------------------------------

def test_qa_negative_config_no_es_7b():
    """config.py no debe tener qwen2.5:7b como default de ollama_model."""
    src = (REPO / "memory/config.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "ollama_model":
                if isinstance(node.value, ast.Constant):
                    assert node.value.value != "qwen2.5:7b", (
                        "config.py todavía tiene qwen2.5:7b como default"
                    )
                    return
    # Si no encontramos el campo, dejamos que el qa_positive falle


def test_qa_negative_aux_llm_no_es_7b():
    """aux_llm.py no debe tener qwen2.5:7b como fallback de OLLAMA_MODEL."""
    src = (REPO / "memory/aux_llm.py").read_text()
    assert "qwen2.5:7b" not in src, (
        "aux_llm.py todavía contiene la cadena 'qwen2.5:7b' — cambio incompleto"
    )


# ---------------------------------------------------------------------------
# qa_control — modelo disponible en Ollama en la máquina actual
# ---------------------------------------------------------------------------

def test_qa_control_ollama_tiene_3b():
    """qwen2.5:3b debe estar disponible en el Ollama local."""
    import subprocess, json
    r = subprocess.run(
        ["ollama", "list"],
        capture_output=True, text=True, timeout=10
    )
    assert "qwen2.5:3b" in r.stdout, (
        f"qwen2.5:3b no aparece en 'ollama list'. Stdout: {r.stdout[:300]}"
    )
