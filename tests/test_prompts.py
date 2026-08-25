from types import SimpleNamespace

from telegram_tools import prompts
from telegram_tools.prompts import BACK, CLEAR, Extra, ask_lines


def reader(*answers):
    values = iter(answers)
    return lambda _prompt: next(values)


def screens(output):
    return "\n".join(output)


def item(name, number):
    return SimpleNamespace(name=name, number=number)


def test_choose_returns_zero_based_index():
    output = []
    result = prompts.choose(["One", "Two"], title="Pick", read=reader("2"), write=output.append)
    assert result == 1
    assert "1. One" in screens(output)
    assert "2. Two" in screens(output)
    assert "0. Back" in screens(output)


def test_choose_returns_back_for_zero():
    result = prompts.choose(["One"], title="Pick", read=reader("0"), write=lambda _: None)
    assert result is BACK


def test_choose_uses_the_given_back_label():
    output = []
    prompts.choose(["One"], title="Pick", read=reader("0"), write=output.append, back_label="Exit")
    assert "0. Exit" in screens(output)


def test_choose_reprints_after_a_bad_answer():
    output = []
    result = prompts.choose(["One"], title="Pick", read=reader("9", "banana", "1"), write=output.append)
    assert result == 0
    assert screens(output).count("1. One") == 3
    assert "Pick one of the numbers listed." in screens(output)


def test_choose_rejects_unicode_digits():
    output = []
    result = prompts.choose(["One"], title="Pick", read=reader("²", "1"), write=output.append)
    assert result == 0
    assert "Pick one of the numbers listed." in screens(output)


def test_pick_returns_the_chosen_item():
    items = [item("a", 1), item("b", 2)]
    result = prompts.pick(items, title="Pick", label=lambda value: value.name, read=reader("2"), write=lambda _: None)
    assert result is items[1]


def test_pick_pages_forward_and_back():
    items = [item(f"chat-{index}", index) for index in range(12)]
    output = []
    # Page 1 shows 9 items and a next row (10); page 2 shows 3 items, a previous row (4), then pick item 1.
    result = prompts.pick(items, title="Pick", label=lambda value: value.name, read=reader("10", "4", "1"), write=output.append)
    assert result is items[0]
    assert "10. Next page (3 more)" in screens(output)
    assert "4. Previous page" in screens(output)


def test_pick_returns_an_extra_key():
    extras = (Extra("filter", "Filter by name"),)
    result = prompts.pick([item("a", 1)], title="Pick", label=lambda value: value.name, read=reader("2"), write=lambda _: None, extras=extras)
    assert result == "filter"


def test_pick_on_an_empty_list_says_so_and_goes_back():
    output = []
    result = prompts.pick([], title="Pick", label=str, read=reader(), write=output.append)
    assert result is BACK
    assert "Nothing to pick from." in screens(output)


def test_pick_many_toggles_and_returns_selected_items():
    items = [item("a", 1), item("b", 2)]
    # Tick 1, tick 2, untick 1, then Continue (row 4: two items + select all + continue).
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("1", "2", "1", "4"), write=lambda _: None)
    assert result == [items[1]]


def test_pick_many_marks_preselected_items():
    items = [item("a", 1), item("b", 2)]
    output = []
    prompts.pick_many(items, title="Rights", label=lambda value: value.name, read=reader("4"), write=output.append, preselected=[items[0]])
    assert "1. [x] a" in screens(output)
    assert "2. [ ] b" in screens(output)


def test_pick_many_select_all_then_continue():
    items = [item("a", 1), item("b", 2)]
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("3", "4"), write=lambda _: None)
    assert result == items


def test_pick_many_refuses_to_continue_with_nothing_ticked():
    items = [item("a", 1)]
    output = []
    # One item, so the rows are: 1 = the item, 2 = Select all, 3 = Continue.
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("3", "1", "3"), write=output.append)
    assert result == [items[0]]
    assert "Tick at least one, or press 0 to go back." in screens(output)


def test_pick_many_returns_back_for_zero():
    result = prompts.pick_many([item("a", 1)], title="Topics", label=lambda value: value.name, read=reader("0"), write=lambda _: None)
    assert result is BACK


def test_pick_many_accepts_several_numbers_separated_by_space_comma_or_both():
    items = [item("a", 1), item("b", 2), item("c", 3)]
    # 3 items: rows are a(1) b(2) c(3) Select all(4) Continue(5).
    for answer in ("2 3", "2,3", "2, 3"):
        result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader(answer, "5"), write=lambda _: None)
        assert result == [items[1], items[2]]


def test_pick_many_single_number_still_pages_forward_and_back():
    # Existing tests already cover a single number on an item row (toggle), on
    # Select all, and on Continue; paging is the one control row pick_many never
    # had its own test for, and it is the one the read/parse rewrite could most
    # easily have broken.
    items = [item(f"i{n}", n) for n in range(10)]
    output = []
    # Page 1: 9 items + Next (row 10). Page 2: 1 item + Previous (row 2). Back from there.
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("10", "2", "0"), write=output.append)
    assert result is BACK
    assert "10. Next page (1 more)" in screens(output)
    assert "2. Previous page" in screens(output)


def test_pick_many_rejects_several_numbers_that_include_a_control_row():
    items = [item("a", 1), item("b", 2), item("c", 3)]
    output = []
    # Rows: a(1) b(2) c(3) Select all(4) Continue(5). "2 4" mixes an item with Select all.
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("2 4", "0"), write=output.append)
    assert result is BACK
    text = screens(output)
    assert "Several at once must all be item numbers, not a page or Continue row." in text
    # Nothing was partially applied: both rows still show unticked on the redraw.
    assert "1. [ ] a" in text
    assert "2. [ ] b" in text


def test_pick_many_rejects_several_numbers_with_one_out_of_range():
    items = [item("a", 1), item("b", 2), item("c", 3)]
    output = []
    result = prompts.pick_many(items, title="Topics", label=lambda value: value.name, read=reader("2 99", "0"), write=output.append)
    assert result is BACK
    assert "Pick one of the numbers listed." in screens(output)


def test_pick_many_prompt_says_several_are_allowed():
    prompts_seen = []

    def read(prompt):
        prompts_seen.append(prompt)
        return "0"

    prompts.pick_many([item("a", 1)], title="Topics", label=lambda value: value.name, read=read, write=lambda _: None)
    assert "2 3" in prompts_seen[0] or "2,3" in prompts_seen[0]


def test_ask_text_returns_the_typed_value():
    assert prompts.ask_text("Name", read=reader(" Harry "), write=lambda _: None) == "Harry"


def test_ask_text_shows_the_current_value_and_cancels_on_blank():
    prompts_seen = []

    def read(prompt):
        prompts_seen.append(prompt)
        return ""

    assert prompts.ask_text("Name", read=read, write=lambda _: None, current="Harry") is BACK
    assert "[Harry]" in prompts_seen[0]


def test_ask_int_rejects_non_numbers_then_returns_the_number():
    output = []
    assert prompts.ask_int("Limit", read=reader("many", "0", "25"), write=output.append) == 25
    assert "Type a whole number of 1 or more." in screens(output)


def test_ask_int_rejects_unicode_digits():
    output = []
    assert prompts.ask_int("Limit", read=reader("²", "5"), write=output.append) == 5
    assert "Type a whole number of 1 or more." in screens(output)


def test_edit_field_keep_returns_back():
    output = []
    result = prompts.edit_field("Bio", "(not set)", read=reader("1"), write=output.append, ask=lambda: "never", allow_clear=True)
    assert result is BACK
    assert "1. Keep it as (not set)" in screens(output)


def test_edit_field_change_returns_the_asked_value():
    result = prompts.edit_field("Bio", "old", read=reader("2"), write=lambda _: None, ask=lambda: "new", allow_clear=True)
    assert result == "new"


def test_edit_field_clear_returns_clear():
    result = prompts.edit_field("Bio", "old", read=reader("3"), write=lambda _: None, ask=lambda: "new", allow_clear=True)
    assert result is CLEAR


def test_edit_field_hides_clear_when_it_is_not_legal():
    output = []
    result = prompts.edit_field("Name", "Harry", read=reader("0"), write=output.append, ask=lambda: "new", allow_clear=False)
    assert result is BACK
    assert "Clear it" not in screens(output)


def test_edit_field_skips_its_screen_when_not_set():
    output = []
    read = reader("deploy")
    result = prompts.edit_field(
        "Contains",
        "(anything)",
        read=read,
        write=output.append,
        ask=lambda: prompts.ask_text("Contains", read=read, write=output.append),
        allow_clear=True,
        is_set=False,
    )
    assert result == "deploy"
    assert output == []


def test_edit_field_still_shows_keep_change_clear_when_set():
    output = []
    result = prompts.edit_field(
        "Contains", "deploy", read=reader("1"), write=output.append, ask=lambda: "never", allow_clear=True, is_set=True
    )
    assert result is BACK
    text = screens(output)
    assert "1. Keep it as deploy" in text
    assert "2. Change it" in text
    assert "3. Clear it" in text


def test_after_action_returns_true_for_enter_and_false_for_zero():
    assert prompts.after_action(read=reader(""), write=lambda _: None) is True
    assert prompts.after_action(read=reader("0"), write=lambda _: None) is False


def test_ask_lines_collects_until_a_lone_dot():
    output = []
    value = ask_lines("Message", read=reader("deploy is green", "all tests pass", "."), write=output.append)

    assert value == "deploy is green\nall tests pass"


def test_ask_lines_takes_a_single_line_too():
    value = ask_lines("Message", read=reader("hi", "."), write=lambda _line: None)

    assert value == "hi"


def test_ask_lines_keeps_blank_lines_inside_the_body():
    value = ask_lines("Message", read=reader("one", "", "three", "."), write=lambda _line: None)

    assert value == "one\n\nthree"


def test_ask_lines_cancels_when_nothing_was_typed():
    assert ask_lines("Message", read=reader("."), write=lambda _line: None) is BACK


def test_ask_lines_cancels_on_a_blank_first_line():
    assert ask_lines("Message", read=reader(""), write=lambda _line: None) is BACK


def test_ask_lines_says_how_to_finish_and_how_to_cancel():
    output = []
    ask_lines("Message", read=reader("hi", "."), write=output.append)

    header = "\n".join(output)
    assert "blank cancels" in header
    assert "." in header


def test_ask_lines_shows_the_current_value_in_the_header():
    output = []
    ask_lines("Message", read=reader("new", "."), write=output.append, current="old body")

    assert "old body" in "\n".join(output)


def test_ask_lines_trailing_blank_lines_are_dropped():
    value = ask_lines("Message", read=reader("body", "", "", "."), write=lambda _line: None)

    assert value == "body"
