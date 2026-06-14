from enum import StrEnum


class RDAPQueryType(StrEnum):
    DOMAIN = "domain"
    IP = "ip"
    AUTNUM = "autnum"
    NAMESERVER = "nameserver"
    ENTITY = "entity"
