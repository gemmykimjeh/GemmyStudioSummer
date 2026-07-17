"""
==============================================================================
playbook.py
==============================================================================

This file contains functions for parsing and manipulating the playbook.

"""
import json
import re
from utils import get_section_slug

def parse_playbook_line(line):
    """Parse a single playbook line to extract components"""
    # Pattern: [id] helpful=X harmful=Y :: content  (X/Y may be float weights)
    pattern = r'\[([^\]]+)\]\s*helpful=(-?\d+(?:\.\d+)?)\s*harmful=(-?\d+(?:\.\d+)?)\s*::\s*(.*)'
    match = re.match(pattern, line.strip())

    if match:
        return {
            'id': match.group(1),
            'helpful': float(match.group(2)),
            'harmful': float(match.group(3)),
            'content': match.group(4),
            'raw_line': line
        }
    return None

def get_next_global_id(playbook_text):
    """Extract highest global ID and return next one"""
    max_id = 0
    lines = playbook_text.strip().split('\n')
    
    for line in lines:
        parsed = parse_playbook_line(line)
        if parsed:
            # Extract numeric part from ID
            id_match = re.search(r'-(\d+)$', parsed['id'])
            if id_match:
                num = int(id_match.group(1))
                max_id = max(max_id, num)
    
    return max_id + 1


def _fmt_count(x):
    """Render a count: integer if whole (52), else 2-decimal float (1.60)."""
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.2f}"


def format_playbook_line(bullet_id, helpful, harmful, content):
    """Format a bullet into playbook line format"""
    return f"[{bullet_id}] helpful={_fmt_count(helpful)} harmful={_fmt_count(harmful)} :: {content}"

def update_bullet_counts(playbook_text, bullet_tags):
    """Update helpful/harmful counts based on tags (Counter layer)"""
    lines = playbook_text.strip().split('\n')
    updated_lines = []
    
    # Create tag lookup - handle both old and new formats
    tag_map = {}
    if isinstance(bullet_tags, list) and len(bullet_tags) > 0:
        for tag in bullet_tags:
            if isinstance(tag, dict):
                # Handle both 'id' and 'bullet' keys for backwards compatibility
                bullet_id = tag.get('id') or tag.get('bullet', '')
                if not bullet_id:
                    continue
                # Grader-aligned path: explicit float weights per side.
                # Legacy path: a 'tag' string → ±1 on the matching side.
                if 'helpful_w' in tag or 'harmful_w' in tag:
                    hw = float(tag.get('helpful_w', 0.0) or 0.0)
                    aw = float(tag.get('harmful_w', 0.0) or 0.0)
                else:
                    t = tag.get('tag', 'neutral')
                    hw = 1.0 if t == 'helpful' else 0.0
                    aw = 1.0 if t == 'harmful' else 0.0
                tag_map[bullet_id] = (hw, aw)
    
    if not tag_map:
        print("Warning: No valid bullet tags found to update counts")
        return playbook_text
    
    for line in lines:
        if line.strip().startswith('#') or not line.strip():
            # Preserve section headers and empty lines
            updated_lines.append(line)
            continue
            
        parsed = parse_playbook_line(line)
        if parsed and parsed['id'] in tag_map:
            hw, aw = tag_map[parsed['id']]
            parsed['helpful'] = round(float(parsed['helpful']) + hw, 3)
            parsed['harmful'] = round(float(parsed['harmful']) + aw, 3)

            # Reconstruct line with updated counts
            new_line = format_playbook_line(
                parsed['id'], parsed['helpful'], parsed['harmful'], parsed['content']
            )
            updated_lines.append(new_line)
        else:
            updated_lines.append(line)

    return '\n'.join(updated_lines)


def prune_harmful_bullets(playbook_text, protected_ids=None, margin=1.0, min_obs=2.0):
    """Actuator for the grader-aligned counts: DELETE learned (non-protected)
    bullets the grader signal has shown to be net-harmful.

    A bullet is pruned only when it has enough evidence
    (helpful + harmful >= ``min_obs``) AND harmful exceeds helpful by at least
    ``margin``. Seed/protected bullets, section headers, and blank lines are
    always kept verbatim. Returns ``(new_playbook_text, removed_ids)``.
    """
    protected = set(protected_ids or ())
    kept, removed = [], []
    for line in playbook_text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            kept.append(line)
            continue
        parsed = parse_playbook_line(line)
        if not parsed:
            kept.append(line)
            continue
        h, a = float(parsed['helpful']), float(parsed['harmful'])
        if (parsed['id'] not in protected) and (h + a) >= min_obs and (a - h) >= margin:
            removed.append(parsed['id'])
            continue
        kept.append(line)
    return '\n'.join(kept), removed


def get_bullet_ids(playbook_text):
    """Return the set of all bullet IDs currently in a playbook.

    Used to capture the immutable *seed* bullet IDs at load time so the
    curator's protected-id guard can never edit/remove them (see
    ``apply_curator_operations(respect_protected=True)``).
    """
    ids = set()
    for line in playbook_text.strip().split('\n'):
        parsed = parse_playbook_line(line)
        if parsed:
            ids.add(parsed['id'])
    return ids


def apply_curator_operations(playbook_text, operations, next_id,
                             respect_protected=False, protected_ids=None):
    """
    Apply curator operations to playbook

    Args:
        respect_protected: when True, mutating ops (UPDATE/MERGE/DELETE) that
            target a protected (seed) bullet are skipped. ADD is always allowed,
            so with the current ADD-only curator this guard is a no-op today; it
            wires seed-immutability for when UPDATE/DELETE are implemented.
        protected_ids: iterable of bullet IDs that must never be mutated/removed.

    TODO: Future Operations (not implemented yet)
    - UPDATE: Rewrite existing bullets to be more accurate or comprehensive
    - MERGE: Combine related bullets into stronger ones
    - CREATE_META: Add high-level strategy sections
    - DELETE: Remove outdated or incorrect bullets (if needed)
    """
    protected_ids = set(protected_ids) if protected_ids else set()
    lines = playbook_text.strip().split('\n')
    
    # Build section map
    sections = {}
    current_section = "general"
    section_line_map = {}  # Track which line each section header is on
    
    for i, line in enumerate(lines):
        if line.strip().startswith('##'):
            # Extract section name and normalize it
            section_header = line.strip()[2:].strip()
            current_section = section_header.lower().replace(' ', '_').replace('&', 'and')
            section_line_map[current_section] = i
            if current_section not in sections:
                sections[current_section] = []
        elif line.strip():
            sections[current_section].append((i, line))
    
    # Process operations
    bullets_to_add = []
    bullets_to_delete = set()

    for op in operations:
        op_type = op['type']

        # Seed-immutability guard: never let a mutating op touch a protected bullet.
        if respect_protected and op_type in ('UPDATE', 'MERGE', 'DELETE'):
            targets = set()
            if op.get('bullet_id'):
                targets.add(op['bullet_id'])
            targets.update(op.get('source_ids', []) or [])
            if targets & protected_ids:
                print(f"  Skipping {op_type} on protected seed bullet(s): "
                      f"{sorted(targets & protected_ids)}")
                continue

        # TODO: Future operation types (not implemented yet)
        # elif op_type == 'UPDATE':
        #     bullet_id = op.get('bullet_id', '')
                    #     new_content = op.get('content', '')
            #     bullets_to_update[bullet_id] = new_content
        # elif op_type == 'MERGE':
        #     source_ids = op.get('source_ids', [])
        #     bullets_to_delete.update(source_ids)
        #     # Add merged bullet logic here
        # elif op_type == 'CREATE_META':
        #     section_name = op.get('section_name', 'META_STRATEGIES')
        #     # Add meta section creation logic here
        
        if op_type == 'DELETE':
            # Remove an existing bullet by id. Protected (seed) bullets are
            # already screened out above when respect_protected is on.
            bid = op.get('bullet_id') or op.get('id')
            if bid:
                bullets_to_delete.add(bid)
                print(f"  Marked bullet {bid} for deletion")
            continue

        if op_type == 'ADD':
            # Normalize section name from operation
            section_raw = op.get('section', 'general')
            section = section_raw.lower().replace(' ', '_').replace('&', 'and')
            
            # Check if section exists, if not use 'others'
            if section not in sections and section != 'general':
                print(f"Warning: Section '{section_raw}' not found, adding to OTHERS")
                section = 'others'
            
            slug = get_section_slug(section)
            new_id = f"{slug}-{next_id:05d}"
            next_id += 1
            
            content = op.get('content', '')

            new_line = format_playbook_line(new_id, 0, 0, content)
            bullets_to_add.append((section, new_line))
            print(f"  Added bullet {new_id} to section {section}")
            

    
    # Rebuild playbook (dropping any DELETE-marked bullets)
    new_lines = []
    for line in lines:
        parsed = parse_playbook_line(line)
        if parsed and parsed['id'] in bullets_to_delete:
            print(f"  Deleted bullet {parsed['id']}")
            continue
        new_lines.append(line)
    
    # Add new bullets to appropriate sections
    final_lines = []
    current_section = None
    
    for line in new_lines:
        if line.strip().startswith('##'):
            # Before moving to new section, add any bullets for current section
            if current_section:
                section_adds = [b for s, b in bullets_to_add if s == current_section]
                final_lines.extend(section_adds)
                # Clear added bullets
                bullets_to_add = [(s, b) for s, b in bullets_to_add if s != current_section]
            
            section_header = line.strip()[2:].strip()
            current_section = section_header.lower().replace(' ', '_').replace('&', 'and')
        final_lines.append(line)
    
    # Add remaining bullets to current section
    if current_section:
        section_adds = [b for s, b in bullets_to_add if s == current_section]
        final_lines.extend(section_adds)
        bullets_to_add = [(s, b) for s, b in bullets_to_add if s != current_section]
    
    # If there are still bullets to add (for sections that don't exist), add them to OTHERS
    if bullets_to_add:
        print(f"Warning: {len(bullets_to_add)} bullets have no matching section, adding to OTHERS")
        others_bullets = [b for s, b in bullets_to_add]
        # Find OTHERS section
        others_idx = -1
        for i, line in enumerate(final_lines):
            if line.strip() == "## OTHERS":
                others_idx = i
                break
        
        if others_idx >= 0:
            # Insert after OTHERS header
            for i, bullet in enumerate(others_bullets):
                final_lines.insert(others_idx + 1 + i, bullet)
        else:
            # Append to end
            final_lines.extend(others_bullets)
    
    return '\n'.join(final_lines), next_id

def get_playbook_stats(playbook_text):
    """Generate statistics about the playbook"""
    lines = playbook_text.strip().split('\n')
    stats = {
        'total_bullets': 0,
        'high_performing': 0,  # helpful > 5, harmful < 2
        'problematic': 0,      # harmful >= helpful
        'unused': 0,           # helpful + harmful = 0
        'by_section': {}
    }
    
    current_section = 'general'
    
    for line in lines:
        if line.strip().startswith('##'):
            current_section = line.strip()[2:].strip()
            continue
            
        parsed = parse_playbook_line(line)
        if parsed:
            stats['total_bullets'] += 1
            
            if parsed['helpful'] > 5 and parsed['harmful'] < 2:
                stats['high_performing'] += 1
            elif parsed['harmful'] >= parsed['helpful'] and parsed['harmful'] > 0:
                stats['problematic'] += 1
            elif parsed['helpful'] + parsed['harmful'] == 0:
                stats['unused'] += 1
            
            if current_section not in stats['by_section']:
                stats['by_section'][current_section] = {'count': 0, 'helpful': 0, 'harmful': 0}
            
            stats['by_section'][current_section]['count'] += 1
            stats['by_section'][current_section]['helpful'] += parsed['helpful']
            stats['by_section'][current_section]['harmful'] += parsed['harmful']
    
    return stats

def extract_json_from_text(text, json_key=None):
    """Extract JSON object from text, handling various formats"""
    try:
        # First, try to parse the entire response as JSON (JSON mode)
        try:
            result = json.loads(text.strip())
            return result
        except json.JSONDecodeError:
            pass
        
        # Fallback: Look for ```json blocks
        json_pattern = r'```json\s*(.*?)\s*```'
        matches = re.findall(json_pattern, text, re.DOTALL | re.IGNORECASE)
        
        if matches:
            # Try each match until we find valid JSON
            for match in matches:
                try:
                    json_str = match.strip()
                    result = json.loads(json_str)
                    return result
                except json.JSONDecodeError:
                    continue
        
        # Improved JSON extraction using balanced brace counting
        # This handles deeply nested structures better
        def find_json_objects(text):
            """Find JSON objects using balanced brace counting"""
            json_objects = []
            i = 0
            while i < len(text):
                if text[i] == '{':
                    # Found start of potential JSON object
                    brace_count = 1
                    start = i
                    i += 1
                    
                    while i < len(text) and brace_count > 0:
                        if text[i] == '{':
                            brace_count += 1
                        elif text[i] == '}':
                            brace_count -= 1
                        elif text[i] == '"':
                            # Handle quoted strings to avoid counting braces inside strings
                            i += 1
                            while i < len(text) and text[i] != '"':
                                if text[i] == '\\':
                                    i += 1  # Skip escaped character
                                i += 1
                        i += 1
                    
                    if brace_count == 0:
                        # Found complete JSON object
                        json_candidate = text[start:i]
                        json_objects.append(json_candidate)
                else:
                    i += 1
            
            return json_objects
        
        # Find all potential JSON objects
        json_objects = find_json_objects(text)
        
        for json_str in json_objects:
            try:
                result = json.loads(json_str)
                return result
            except json.JSONDecodeError:
                continue
                
    except Exception as e:
        print(f"Failed to extract JSON: {e}")
        if len(text) > 500:
            print(f"Raw content preview:\n{text[:500]}...")
        else:
            print(f"Raw content:\n{text}")
        
    return None

def extract_playbook_bullets(playbook_text, bullet_ids):
    """
    Extract specific bullet points from playbook based on bullet_ids.
    
    Args:
        playbook_text (str): The full playbook text
        bullet_ids (list): List of bullet IDs to extract
    
    Returns:
        str: Formatted playbook content containing only the specified bullets
    """
    if not bullet_ids:
        return "(No bullets used by generator)"
    
    lines = playbook_text.strip().split('\n')
    found_bullets = []
    
    for line in lines:
        if line.strip():  # Skip empty lines
            parsed = parse_playbook_line(line)
            if parsed and parsed['id'] in bullet_ids:
                found_bullets.append({
                    'id': parsed['id'],
                    'content': parsed['content'],
                    'helpful': parsed['helpful'],
                    'harmful': parsed['harmful']
                })
    
    if not found_bullets:
        return "(Generator referenced bullet IDs but none were found in playbook)"
    
    # Format the bullets for reflector input
    formatted_bullets = []
    for bullet in found_bullets:
        formatted_bullets.append(f"[{bullet['id']}] helpful={bullet['helpful']} harmful={bullet['harmful']} :: {bullet['content']}")

    return '\n'.join(formatted_bullets)


def split_seed_learned(playbook_str, protected_ids):
    """Split a playbook STRING into (seed_lines, learned_str) at the line level.

    - seed_lines: raw lines of protected (seed) bullets, **verbatim**.
    - learned_str: the playbook with seed bullet lines removed, original section
      headers and bullet lines kept verbatim; a header whose bullets were all
      seeds (now empty) is dropped.

    Operates on raw lines and uses ``parse_playbook_line`` ONLY to read the id
    for the protected check (the parsed dict has no section field) — so nothing
    is re-rendered and there is no formatting drift.
    """
    protected = set(protected_ids or ())
    seed_lines = []
    chunks = []          # list[(header_line_or_None, [learned_bullet_lines])]
    cur_header, cur_bullets = None, []

    def _flush():
        if cur_bullets:
            chunks.append((cur_header, list(cur_bullets)))

    for line in playbook_str.splitlines():
        if line.strip().startswith('##'):
            _flush()
            cur_header, cur_bullets = line, []
            continue
        parsed = parse_playbook_line(line)
        if parsed is None:
            continue                      # blank / non-bullet line
        if parsed['id'] in protected:
            seed_lines.append(line)       # verbatim
        else:
            cur_bullets.append(line)      # verbatim
    _flush()

    blocks = []
    for header, bullets in chunks:
        blocks.append((header + '\n' if header else '') + '\n'.join(bullets))
    return seed_lines, '\n\n'.join(blocks)


def render_for_generator(parts):
    """Generator-only view: hoist ALL seed bullets from every part into ONE
    trailing STANDING RULES block; learned bullets go above under LEARNED HINTS,
    each part's learned block prefixed by its label (e.g. the concrete/abstract
    banner). Seeds sit last because recency dominates instruction-following.

    Args:
        parts: list of (label, playbook_str, protected_ids). label may be "".
    """
    all_seeds, learned_blocks = [], []
    for label, pb_str, protected in parts:
        seeds, learned = split_seed_learned(pb_str, protected)
        all_seeds.extend(seeds)
        if learned.strip():
            learned_blocks.append((label + '\n' if label else '') + learned)

    out = []
    if learned_blocks:
        out.append(
            "## LEARNED HINTS — extracted from past tasks. "
            "Ignore any that do not fit the current task.\n"
            + '\n\n'.join(learned_blocks)
        )
    if all_seeds:
        out.append(
            "## STANDING RULES — violating any of these fails the task. "
            "These take precedence over everything above.\n"
            + '\n'.join(all_seeds)        # flat, no section headers
        )
    return '\n\n'.join(out)
