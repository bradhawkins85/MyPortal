from pathlib import Path


def test_matrix_chat_assign_uses_header_action_menu_for_new_rule():
    source = Path('app/templates/admin/matrix_chat_assign.html').read_text()
    assert 'page_header_overflow' in source
    assert 'data-new-rule-modal-open' in source
    assert 'id="rule-modal"' in source


def test_matrix_chat_assign_table_rows_use_action_menu_for_edit_delete():
    source = Path('app/templates/admin/matrix_chat_assign.html').read_text()
    assert 'header-title-menu__dropdown' in source
    assert 'data-edit-rule-modal-open' in source
    assert '/rules/{{ rule.id }}/delete' in source


def test_matrix_chat_assign_no_longer_uses_sidebar_layout_panel():
    source = Path('app/templates/admin/matrix_chat_assign.html').read_text()
    assert 'management management--single' in source
    assert 'management__sidebar' not in source
