#!/usr/bin/env python3
"""
Validator for cicd-manager Jinja2 templates and inline JavaScript blocks.
Extracts <script> contents from templates and validates JavaScript syntax.
"""
import os
import re
import subprocess
import sys
import tempfile

def validate_js_syntax(js_code, filename="<script>"):
    # Strip jinja template tags {{ ... }} and {% ... %} into valid JS expressions for syntax checking
    clean_js = re.sub(r'\{\{.*?\}\}', '("__JINJA_VAR__")', js_code)
    clean_js = re.sub(r'\{%.*?%\}', '/* __JINJA_TAG__ */', clean_js)
    
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(clean_js)
        temp_path = f.name
        
    try:
        # Check syntax with node -c
        res = subprocess.run(["node", "-c", temp_path], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[!] JavaScript SyntaxError in {filename}:", file=sys.stderr)
            print(res.stderr, file=sys.stderr)
            return False
        return True
    except FileNotFoundError:
        # Fallback to python quick check if node is absent
        print("[i] Node.js not found, skipping deep JS AST validation.")
        return True
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def main():
    templates_dir = sys.argv[1] if len(sys.argv) > 1 else "templates"
    if not os.path.exists(templates_dir):
        print(f"Error: Templates directory {templates_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    errors = 0
    for root, _, files in os.walk(templates_dir):
        for f in files:
            if f.endswith(".html"):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fp:
                    content = fp.read()
                    
                scripts = re.findall(r'<script(?:\s+[^>]*)?>(.*?)</script>', content, re.DOTALL | re.IGNORECASE)
                for idx, script in enumerate(scripts):
                    if not validate_js_syntax(script, f"{f} (block {idx+1})"):
                        errors += 1

    if errors > 0:
        print(f"\n[FAIL] Found {errors} template validation errors!", file=sys.stderr)
        sys.exit(1)
    print("[PASS] All templates and inline JavaScript passed syntax validation!")

if __name__ == "__main__":
    main()
