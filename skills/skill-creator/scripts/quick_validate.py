#!/usr/bin/env python3
"""
Quick validation script for skills - minimal version
"""

import sys
import datetime as _datetime
import json
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # Keep simple metadata validation usable in a clean install.
    yaml = None


class FrontmatterError(ValueError):
    """A frontmatter value is outside the validator's deliberately small YAML subset."""


if yaml is not None:
    class _UniqueSafeLoader(yaml.SafeLoader):
        """SafeLoader variant that makes duplicate metadata keys invalid."""

        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in mapping:
                    raise FrontmatterError(f"duplicate frontmatter key: {key}")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping


def _normalise_yaml(value):
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalise_yaml(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_yaml(item) for item in value]
    return value


def _split_flow_items(raw: str) -> list[str]:
    items = []
    current = []
    quote = None
    for char in raw:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == ',':
            items.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if quote:
        raise FrontmatterError("Unmatched quote in flow sequence")
    tail = ''.join(current).strip()
    if tail:
        items.append(tail)
    elif items:
        raise FrontmatterError("Trailing comma in flow sequence")
    return items


def _parse_scalar(raw: str):
    """Parse only scalar/flow-sequence forms used by Codex skill metadata.

    This is the portable fallback for environments without the declared
    PyYAML dependency; full nested YAML is handled by PyYAML when available.
    """
    if not raw:
        return None
    if raw[0] in "'\"":
        if len(raw) < 2 or raw[-1] != raw[0]:
            raise FrontmatterError("Unmatched quote")
        if raw[0] == '"':
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FrontmatterError(f"Invalid double-quoted scalar: {exc.msg}") from exc
        inner = raw[1:-1]
        index = 0
        while index < len(inner):
            if inner[index] == "'":
                if index + 1 >= len(inner) or inner[index + 1] != "'":
                    raise FrontmatterError("Unescaped single quote")
                index += 2
            else:
                index += 1
        return inner.replace("''", "'")
    if raw[0] == '[':
        if not raw.endswith(']'):
            raise FrontmatterError("Unterminated flow sequence")
        return [_parse_scalar(item) for item in _split_flow_items(raw[1:-1])]
    if raw[0] == '{':
        raise FrontmatterError("Flow mappings are not supported")
    if raw in ('true', 'True', 'TRUE'):
        return True
    if raw in ('false', 'False', 'FALSE'):
        return False
    if raw in ('null', 'Null', 'NULL', '~'):
        return None
    if re.fullmatch(r"[+-]?(?:\d[\d_]*|\d[\d_]*\.\d+|\d[\d_]*[eE][+-]?\d+)", raw):
        raise FrontmatterError("numeric metadata requires a quoted string")
    if raw in (']', '}'):
        raise FrontmatterError("Unexpected flow collection terminator")
    return raw


def parse_frontmatter(text):
    """Parse the strict YAML subset needed for skill metadata."""
    values = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith('#'):
            index += 1
            continue
        if ':' not in line or line[:1].isspace():
            raise ValueError(f"Invalid frontmatter line: {line}")
        key, raw = line.split(':', 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            raise ValueError("Frontmatter key cannot be empty")
        if key in values:
            raise FrontmatterError(f"duplicate frontmatter key: {key}")
        if raw in ('>', '|', '>-', '|-'):
            continuation = []
            index += 1
            while index < len(lines) and (lines[index].startswith('  ') or lines[index].startswith('\t')):
                continuation.append(lines[index].strip())
                index += 1
            values[key] = ('\n' if raw.startswith('|') else ' ').join(continuation)
            continue
        if not raw and index + 1 < len(lines) and lines[index + 1].startswith('  - '):
            sequence = []
            index += 1
            while index < len(lines) and lines[index].startswith('  - '):
                sequence.append(_parse_scalar(lines[index][4:].strip()))
                index += 1
            values[key] = sequence
            continue
        if not raw and index + 1 < len(lines) and (lines[index + 1].startswith('  ') or lines[index + 1].startswith('\t')):
            raise FrontmatterError("nested mappings require PyYAML; install the declared PyYAML dependency")
        values[key] = _parse_scalar(raw)
        index += 1
    return values

def validate_skill(skill_path):
    """Basic validation of a skill"""
    skill_path = Path(skill_path)

    # Check SKILL.md exists
    skill_md = skill_path / 'SKILL.md'
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and validate frontmatter
    content = skill_md.read_text()
    if not content.startswith('---'):
        return False, "No YAML frontmatter found"

    # Extract frontmatter
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    # Parse YAML frontmatter
    try:
        if yaml is not None:
            frontmatter = _normalise_yaml(yaml.load(frontmatter_text, Loader=_UniqueSafeLoader))
        else:
            frontmatter = parse_frontmatter(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except (FrontmatterError, ValueError, TypeError) as e:
        return False, f"Invalid YAML in frontmatter: {e}"
    except Exception as e:
        if yaml is not None and isinstance(e, yaml.YAMLError):
            return False, f"Invalid YAML in frontmatter: {e}"
        raise

    # Define allowed properties
    ALLOWED_PROPERTIES = {
        'name',
        'description',
        'license',
        'allowed-tools',
        'metadata',
        'compatibility',
        'disable-model-invocation',
        'references',
        'user-invocable',
        'category',
        'created',
        'updated',
        'status',
        'purpose',
        'argument-hint',
        'team',
        'agent_id',
        'primary_model',
        'model',
        'fallback_models',
        'execution_provider',
        'execution_mode',
        'model_rationale',
        'model_reasoning_effort',
        'upgrade_policy',
        'cost_tier',
        'long_run_preferred',
    }

    # Check for unexpected properties (excluding nested keys under metadata)
    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}. "
            f"Allowed properties are: {', '.join(sorted(ALLOWED_PROPERTIES))}"
        )

    expected_types = {
        key: str
        for key in ALLOWED_PROPERTIES
        if key not in {'user-invocable', 'disable-model-invocation', 'long_run_preferred', 'metadata', 'references', 'fallback_models'}
    }
    expected_types.update({
        'user-invocable': bool,
        'disable-model-invocation': bool,
        'long_run_preferred': bool,
        'metadata': dict,
        'references': list,
        'fallback_models': list,
    })
    for key, expected in expected_types.items():
        if key in frontmatter and not isinstance(frontmatter[key], expected):
            return False, f"{key} must be a {expected.__name__}, got {type(frontmatter[key]).__name__}"

    # Check required fields
    if 'name' not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if 'description' not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    # Extract name for validation
    name = frontmatter.get('name', '')
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
        # Check naming convention (kebab-case: lowercase with hyphens)
        if not re.match(r'^[a-z0-9-]+$', name):
            return False, f"Name '{name}' should be kebab-case (lowercase letters, digits, and hyphens only)"
        if name.startswith('-') or name.endswith('-') or '--' in name:
            return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
        # Check name length (max 64 characters per spec)
        if len(name) > 64:
            return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters."

    # Extract and validate description
    description = frontmatter.get('description', '')
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        # Check for angle brackets
        if '<' in description or '>' in description:
            return False, "Description cannot contain angle brackets (< or >)"
        # Check description length (max 1024 characters per spec)
        if len(description) > 1024:
            return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters."

    # Validate compatibility field if present (optional)
    compatibility = frontmatter.get('compatibility', '')
    if compatibility:
        if not isinstance(compatibility, str):
            return False, f"Compatibility must be a string, got {type(compatibility).__name__}"
        if len(compatibility) > 500:
            return False, f"Compatibility is too long ({len(compatibility)} characters). Maximum is 500 characters."

    return True, "Skill is valid!"

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
