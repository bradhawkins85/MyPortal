from app.services import trello as trello_service


def test_strip_html_removes_formatting_and_preserves_text():
    body = (
        "<div>I found the login page without any issue but it's my actual login "
        "that is complaining, can you please check my account still exists as an admin?</div>"
        "<div>I also tried with my email address but it also won't let me in on there.</div>"
    )

    assert trello_service._strip_html(body) == (
        "I found the login page without any issue but it's my actual login "
        "that is complaining, can you please check my account still exists as an admin?\n"
        "I also tried with my email address but it also won't let me in on there."
    )


def test_strip_html_parses_entity_encoded_markup_and_preserves_line_breaks():
    body = (
        "&lt;div&gt;&lt;span&gt;First line&lt;/span&gt;&lt;/div&gt;"
        "&lt;div&gt;&lt;span&gt;Second line&lt;/span&gt;&lt;br&gt;Third line&lt;/div&gt;"
    )

    assert trello_service._strip_html(body) == "First line\nSecond line\nThird line"


def test_strip_html_parses_doubly_encoded_editor_markup():
    body = (
        "&amp;lt;div&amp;gt;&amp;lt;br&amp;gt;&amp;lt;/div&amp;gt;"
        "&amp;lt;div&amp;gt;Ticket update&amp;lt;/div&amp;gt;"
    )

    assert trello_service._strip_html(body) == "Ticket update"


def test_strip_html_renders_ticket_images_as_absolute_markdown():
    body = '<div>Screenshot attached</div><img src="/api/tickets/25055/attachments/9977/download" alt="">'

    assert trello_service._strip_html(
        body,
        image_base_url="https://portal.example.com",
    ) == (
        "Screenshot attached\n"
        "![image](https://portal.example.com/api/tickets/25055/attachments/9977/download)"
    )
