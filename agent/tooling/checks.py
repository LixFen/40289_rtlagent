"""Conservative interface diagnostics, not a Verilog parser or correctness judge."""
import re


def uncomment(code):
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S)


def expected_module(prompt, interface=''):
    """优先读取明确模块名或 HDL 声明，不把题面中的 module of 当成模块名。"""
    identifier = r'([A-Za-z_][A-Za-z0-9_$]*)'
    patterns = (r'\bmodule\s+name\s*:\s*'+identifier,
                r'\bmodule\s+named\s+'+identifier,
                r'\bmodule\s+(?:(?:automatic|static)\s+)?'+identifier+r'\s*(?=[#(;])',
                r'\bmodule\s+'+identifier+r'(?=\s*$|[.;]|\s+(?:with|that)\b)')
    for specification in (interface, prompt):
        for pattern in patterns:
            match = re.search(pattern, uncomment(specification), re.I | re.M)
            if match:
                return match[1]
    return None


def procedural_net_errors(code):
    """Catch the observed net-vs-variable failure on simple output declarations."""
    variables, outputs = set(), set()
    for match in re.finditer(r"\b(reg|logic|output)\b(.*?)(?=\b(?:input|output|inout)\b|[;)])", code, re.S):
        kind, body = match.groups()
        is_variable = kind in ('reg', 'logic') or re.search(r'\b(?:reg|logic)\b', body)
        body = re.sub(r'\[[^]]*\]', '', body)
        body = re.sub(r'\b(?:wire|reg|logic|signed|unsigned)\b', '', body)
        for item in body.split(','):
            name = item.strip()
            if re.fullmatch(r'\w+', name):
                if kind == 'output':
                    outputs.add(name)
                if is_variable:
                    variables.add(name)
    assigned = set(re.findall(r'(?:^|[;\n]|\bbegin\b|\belse\b)\s*(\w+)\s*(?:\[[^]]*\]\s*)?(?:<=|=(?!=))', code, re.M))
    return ['Procedural output ' + name + ' must be declared reg or logic, not a wire net'
            for name in sorted((outputs - variables) & assigned)]


def interface_errors(prompt, interface, code):
    code = uncomment(code)
    name = expected_module(prompt, interface)
    target = re.search(r'\bmodule\s+'+re.escape(name)+r'\b.*?\bendmodule\b',code,re.S) if name else None
    checked_code = target[0] if target else code
    errors = procedural_net_errors(checked_code)
    if name and not re.search(r"\bmodule\s+" + re.escape(name) + r"\b", code):
        errors.append("Expected module " + name)
    if not re.search(r"\bendmodule\b", code):
        errors.append("Missing endmodule")
    # Only check supported, explicit port declarations; report coverage separately.
    expected = re.findall(r"(?m)^\s*-\s*(input|output|inout)\s+(\w+)(?:\s*\((\d+)\s+bits?\))?", prompt)
    if not expected:
        return errors, 0
    declared = {}
    # ANSI and non-ANSI declarations, including grouped names and numeric ranges.
    for match in re.finditer(r"\b(input|output|inout)\b(.*?)(?=\b(?:input|output|inout)\b|[;)])", checked_code, re.S):
        direction, body = match.groups()
        width = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", body)
        count = abs(int(width[1]) - int(width[2])) + 1 if width else 1
        symbolic = "[" in body and width is None
        body = re.sub(r"\[[^]]*\]", "", body)
        body = re.sub(r"\b(?:wire|reg|logic|signed|unsigned)\b", "", body)
        for port in body.split(","):
            port = port.strip()
            if re.fullmatch(r"\w+", port):
                declared[port] = (direction, None if symbolic else count)
    for direction, port, width in expected:
        actual = declared.get(port)
        if actual is None:
            errors.append("Missing declared port " + port)
        elif actual[0] != direction or (actual[1] is not None and actual[1] != int(width or 1)):
            errors.append("Wrong direction or width for " + port)
    return errors, len(expected)
