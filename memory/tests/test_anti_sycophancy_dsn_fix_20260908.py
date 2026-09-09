"""Tests para fix de DSN hardcodeado en memory/anti_sycophancy.py."""
import ast, subprocess, sys, os
from pathlib import Path

SRC = Path("memory/anti_sycophancy.py")


def _get_db_url_node():
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "DB_URL":
                    return node.value
    return None


def test_qa_positive_db_url_usa_environ():
    """DB_URL debe ser os.environ.get(...), no un literal."""
    node = _get_db_url_node()
    assert node is not None, "DB_URL no encontrado"
    assert isinstance(node, ast.Call), "DB_URL no es una llamada a función"
    func = node.func
    assert isinstance(func, ast.Attribute) and func.attr == "get", "No es .get()"
    # el objeto debe ser os.environ
    val = func.value
    assert isinstance(val, ast.Attribute) and val.attr == "environ", "No es os.environ"


def test_qa_positive_no_literal_con_password():
    """No debe haber postgresql://user:PASSWORD@ con password no vacío."""
    import re
    content = SRC.read_text()
    matches = re.findall(r'postgresql://[^:]+:([^@]+)@', content)
    for m in matches:
        assert m == "" or m.startswith("$"), f"Credencial literal encontrada: {m[:4]}..."


def test_qa_negative_no_redactado():
    """La palabra REDACTADO no debe aparecer en el archivo."""
    assert "REDACTADO" not in SRC.read_text()


def test_qa_negative_no_hardcoded_literal():
    """DB_URL no debe ser un string literal."""
    node = _get_db_url_node()
    assert node is not None
    assert not isinstance(node, ast.Constant), "DB_URL es string literal hardcodeado"


def test_qa_control_import_os():
    """El módulo os debe estar importado (necesario para os.environ.get)."""
    tree = ast.parse(SRC.read_text())
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
    names = [alias.name for imp in imports for alias in imp.names]
    assert "os" in names, "os no está importado"
