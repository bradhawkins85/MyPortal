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


def test_strip_html_renders_ticket_images_as_absolute_markdown():
    body = '<div>Screenshot attached</div><img src="/api/tickets/25055/attachments/9977/download" alt="">'

    assert trello_service._strip_html(
        body,
        image_base_url="https://portal.example.com",
    ) == (
        "Screenshot attached\n"
        "![image](https://portal.example.com/api/tickets/25055/attachments/9977/download)"
    )
