import re
import os

def fix_type_hints_in_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()

    # --- 1. Safer Optional replacements (only in annotations) ---
    # Replace in return annotations (def ... -> Optional[X]:)
    code = re.sub(r'(->\s*)([\w\.]+)\s*\|\s*None', r'\1Optional[\2]', code)
    code = re.sub(r'(->\s*)None\s*\|\s*([\w\.]+)', r'\1Optional[\2]', code)

    # Replace in parameter/variable annotations (name: Optional[X])
    # Use a callback so we don't affect runtime "|" expressions outside annotations
    def _optional_in_annotation(match: re.Match) -> str:
        prefix = match.group(1)
        annot = match.group(2)
        # Only simple X | None or None | X
        annot = re.sub(r'(^|\b)([\w\.]+)\s*\|\s*None(\b|$)', r'Optional[\2]', annot)
        annot = re.sub(r'(^|\b)None\s*\|\s*([\w\.]+)(\b|$)', r'Optional[\2]', annot)
        return f"{prefix}{annot}"

    # name: <annotation> (no equals sign immediately after annotation)
    code = re.sub(r'(\b\w+\s*:\s*)([^=\n]+)', _optional_in_annotation, code)

    # --- 3. Replace built-in generics with typing equivalents ---
    # Handles nested generics (e.g., List[Dict[str, int]])
    code = re.sub(r'\blist\[(.*?)\]', r'List[\1]', code)
    code = re.sub(r'\bdict\[(.*?)\]', r'Dict[\1]', code)
    code = re.sub(r'\bset\[(.*?)\]', r'Set[\1]', code)
    code = re.sub(r'\btuple\[(.*?)\]', r'Tuple[\1]', code)

    # --- 4. Add missing typing imports if needed ---
    imports = []
    if re.search(r'\bOptional\[', code) and 'from typing import Optional' not in code:
        imports.append('Optional')
    if re.search(r'\bList\[', code) and 'from typing import List' not in code:
        imports.append('List')
    if re.search(r'\bDict\[', code) and 'from typing import Dict' not in code:
        imports.append('Dict')
    if re.search(r'\bSet\[', code) and 'from typing import Set' not in code:
        imports.append('Set')
    if re.search(r'\bTuple\[', code) and 'from typing import Tuple' not in code:
        imports.append('Tuple')
    # For TypedDict and NotRequired (Python <3.8)
    if re.search(r'\bTypedDict\b', code) and 'from typing import TypedDict' not in code and 'from typing_extensions import TypedDict' not in code:
        code = re.sub(
            r'(import [^\n]+\n)',
            r"\1try:\n    from typing import TypedDict, NotRequired\nexcept ImportError:\n    from typing_extensions import TypedDict, NotRequired\n",
            code,
            count=1
        )
    elif re.search(r'\bNotRequired\b', code) and 'from typing import NotRequired' not in code and 'from typing_extensions import NotRequired' not in code:
        code = re.sub(
            r'(import [^\n]+\n)',
            r"\1try:\n    from typing import NotRequired\nexcept ImportError:\n    from typing_extensions import NotRequired\n",
            code,
            count=1
        )
    # Add the rest of the imports
    if imports:
        # Try to insert after the first import line; if no imports, prepend at top
        if re.search(r'\bimport\s+[^\n]+\n', code):
            code = re.sub(
                r'(import [^\n]+\n)',
                r'\1from typing import ' + ', '.join(imports) + '\n',
                code,
                count=1
            )
        else:
            code = 'from typing import ' + ', '.join(imports) + '\n' + code

    # --- 5. Warn about match/case (Python 3.10+ only) ---
    if re.search(r'\bmatch\b.*:', code) and re.search(r'\bcase\b.*:', code):
        print(f"WARNING: 'match/case' syntax found in {filepath}. This is not supported in Python <3.10.")

    # --- 6. Write back the fixed code ---
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(code)

# Walk project files but skip build/vendor directories
SKIP_DIRS = {'.git', '.hg', '.svn', '.buildozer', 'build', 'dist', 'venv', '.venv', '__pycache__'}

for root, dirs, files in os.walk('.'):
    # Prune directories we don't want to touch
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.') or d == '.']
    for file in files:
        if file.endswith('.py'):
            fix_type_hints_in_file(os.path.join(root, file))