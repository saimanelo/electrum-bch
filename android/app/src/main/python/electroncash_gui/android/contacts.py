from electroncash.address import Address
from electroncash.contacts import Contact


def get_contacts(wallet, only_token_aware=False):
    def is_token_aware(contact: Contact):
        return contact.type == "tokenaddr"
    contacts = wallet.contacts.get_all()
    if only_token_aware:
        contacts = filter(is_token_aware, contacts)
    return sorted(contacts, key=lambda contact: contact.name)
