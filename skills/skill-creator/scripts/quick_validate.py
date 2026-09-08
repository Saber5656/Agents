#!/usr/bin/env python3
"""
Quick validation script for skills - minimal version
"""

import sys
import datetime as _datetime
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # Report setup instructions without an import traceback.
    yaml = None


class FrontmatterError(ValueError):
    """Frontmatter violates the metadata contract."""


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

    # One parser keeps metadata semantics identical across installations.
    if yaml is None:
        return False, ("PyYAML is required: install the declared dependency with "
                       "python3 -m pip install -r "
                       "skills/skill-creator/requirements-quick-validate.txt")
    try:
        frontmatter = _normalise_yaml(yaml.load(frontmatter_text, Loader=_UniqueSafeLoader))
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
    if not name:
        return False, 'Name cannot be empty'
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
    if not description:
        return False, 'Description cannot be empty'
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
