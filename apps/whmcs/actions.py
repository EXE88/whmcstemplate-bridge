"""
The WHMCS actions this bridge is allowed to invoke.

This is an allowlist, not documentation. ``WhmcsClient.call`` refuses anything
that is not listed here, so a bug (or an injected parameter) can never turn into
``DeleteClient``. Add an action deliberately, next to the service that needs it.

Reference: https://developers.whmcs.com/api/api-index/
"""

from enum import StrEnum


class Action(StrEnum):
    # --- system ---
    WHMCS_DETAILS = "WhmcsDetails"
    GET_CURRENCIES = "GetCurrencies"
    GET_PAYMENT_METHODS = "GetPaymentMethods"

    # --- authentication / clients ---
    VALIDATE_LOGIN = "ValidateLogin"
    GET_CLIENTS = "GetClients"
    GET_CLIENTS_DETAILS = "GetClientsDetails"
    ADD_CLIENT = "AddClient"
    UPDATE_CLIENT = "UpdateClient"
    GET_CONTACTS = "GetContacts"
    ADD_CONTACT = "AddContact"
    UPDATE_CONTACT = "UpdateContact"
    GET_EMAILS = "GetEmails"

    # --- catalogue / ordering ---
    GET_PRODUCTS = "GetProducts"
    GET_PROMOTIONS = "GetPromotions"
    GET_TLD_PRICING = "GetTLDPricing"
    ADD_ORDER = "AddOrder"
    GET_ORDERS = "GetOrders"
    ACCEPT_ORDER = "AcceptOrder"
    CANCEL_ORDER = "CancelOrder"

    # --- services / provisioning ---
    GET_CLIENTS_PRODUCTS = "GetClientsProducts"
    GET_CLIENTS_ADDONS = "GetClientsAddons"
    UPGRADE_PRODUCT = "UpgradeProduct"
    MODULE_CHANGE_PW = "ModuleChangePw"
    ADD_CANCEL_REQUEST = "AddCancelRequest"

    # --- domains ---
    GET_CLIENTS_DOMAINS = "GetClientsDomains"
    DOMAIN_WHOIS = "DomainWhois"
    DOMAIN_GET_NAMESERVERS = "DomainGetNameservers"
    DOMAIN_UPDATE_NAMESERVERS = "DomainUpdateNameservers"
    DOMAIN_GET_LOCKING_STATUS = "DomainGetLockingStatus"
    DOMAIN_UPDATE_LOCKING_STATUS = "DomainUpdateLockingStatus"
    DOMAIN_REQUEST_EPP = "DomainRequestEPP"
    DOMAIN_RENEW = "DomainRenew"
    DOMAIN_TOGGLE_ID_PROTECT = "DomainToggleIdProtect"

    # --- billing ---
    GET_INVOICES = "GetInvoices"
    GET_INVOICE = "GetInvoice"
    GET_TRANSACTIONS = "GetTransactions"
    GET_CREDITS = "GetCredits"
    GET_QUOTES = "GetQuotes"
    ACCEPT_QUOTE = "AcceptQuote"
    GET_PAY_METHODS = "GetPayMethods"

    # --- support ---
    GET_TICKETS = "GetTickets"
    GET_TICKET = "GetTicket"
    OPEN_TICKET = "OpenTicket"
    ADD_TICKET_REPLY = "AddTicketReply"
    UPDATE_TICKET = "UpdateTicket"
    GET_SUPPORT_DEPARTMENTS = "GetSupportDepartments"
    GET_TICKET_ATTACHMENT = "GetTicketAttachment"
    GET_ANNOUNCEMENTS = "GetAnnouncements"


ALLOWED_ACTIONS: frozenset[str] = frozenset(a.value for a in Action)

#: Actions the bridge must never call on behalf of an end user, even if some
#: future code path adds them to :class:`Action`. Belt and braces.
FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
    {
        "DeleteClient",
        "DeleteOrder",
        "DeleteTicket",
        "DeleteQuote",
        "ModuleTerminate",
        "ModuleSuspend",
        "AddBannedIp",
        "DecryptPassword",
        "GetClientPassword",
        "SetConfigurationValue",
        "SendAdminEmail",
        "AddCredit",
    }
)
