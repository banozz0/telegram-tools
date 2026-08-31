from telegram_tools.columns import cell, pad, width

# Two chat titles the terminal draws at the same width and len() does not:
# the shape of the bug this helper exists for.
VAULT = "📚 Vaults"
SYSTEM = "⚙️ Alerts"


def test_width_counts_columns_not_codepoints():
    assert width("Hermes") == 6
    # An emoji draws two columns from one codepoint.
    assert width("🔨 Dobby") == 8 == len("🔨 Dobby") + 1
    # A variation selector is a codepoint that draws nothing of its own; it
    # hands its column to the character it follows, so ⚙️ is two columns
    # from two codepoints.
    assert width(SYSTEM) == 9 == len(SYSTEM)
    assert width(VAULT) == 9 == len(VAULT) + 1


def test_width_ignores_a_combining_mark():
    assert width("é") == 1


def test_cell_lines_the_next_column_up_where_len_did_not():
    # The regression: padding on len() put these two rows a column apart.
    assert width(cell(VAULT, 32)) == width(cell(SYSTEM, 32)) == 32
    assert len(f"{VAULT:<32}") == len(f"{SYSTEM:<32}")  # ...which len() calls equal
    assert width(f"{VAULT:<32}") != width(f"{SYSTEM:<32}")  # ...and the terminal does not


def test_cell_cuts_a_name_that_does_not_fit():
    assert cell("Hermes deployments", 10) == "Hermes dep"
    assert width(cell("Hermes deployments", 10)) == 10


def test_cell_never_cuts_an_emoji_in_half_or_overflows():
    # 🔨 is two columns, so it cannot be the ninth of nine: the row stops short
    # and is padded rather than drawn one column too wide.
    padded = cell("12345678🔨", 9)
    assert padded == "12345678 "
    assert width(padded) == 9


def test_pad_leaves_a_long_name_alone():
    # pad never cuts, for a listing where the name is what the reader came for.
    assert pad("Hermes deployments", 10) == "Hermes deployments"
    assert width(pad(VAULT, 32)) == 32
