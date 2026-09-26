def format_emirates_id(value, fallback="—"):
    """Display a 15-digit Emirates ID as 784-YYYY-NNNNNNN-N."""
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) == 15:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}"
    return text or fallback


def masked_emirates_id(value, fallback="Not supplied"):
    """Keep a masked ID grouped in the same dashed display format."""
    digits = "".join(
        character for character in str(value or "") if character.isdigit()
    )
    if len(digits) == 15:
        return f"•••-••••-••••{digits[-4:-1]}-{digits[-1]}"
    return fallback

