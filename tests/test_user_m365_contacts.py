from app.services import user_m365_contacts


def test_match_contact_phones_requires_requester_name_match():
    contacts = [
        {"displayName": "Ada Lovelace", "mobilePhone": "0400 111 222", "businessPhones": ["02 1234 5678"]},
        {"displayName": "Grace Hopper", "mobilePhone": "0400 999 999"},
    ]
    assert user_m365_contacts.match_contact_phones("Ada Lovelace", contacts) == [
        {"name": "Ada Lovelace", "phone": "02 1234 5678"},
        {"name": "Ada Lovelace", "phone": "0400 111 222"},
    ]


def test_match_contact_phones_deduplicates_formatted_numbers():
    contacts = [{
        "displayName": "Ada Lovelace", "mobilePhone": "+61 400 111 222",
        "businessPhones": ["+61 (400) 111-222"], "homePhones": [],
    }]
    assert user_m365_contacts.match_contact_phones("Ada Lovelace", contacts) == [
        {"name": "Ada Lovelace", "phone": "+61 400 111 222"},
    ]
