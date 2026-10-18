from rest_framework.permissions import BasePermission

from .auth import CUSTOMER, SHOP_OWNER, SYSTEM


class IsCustomer(BasePermission):
    """Any signed-in shopper. Shop owners can also shop."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and (user.has_role(CUSTOMER) or user.has_role(SHOP_OWNER)))


class IsShopOwner(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.has_role(SHOP_OWNER))


class IsSystem(BasePermission):
    """A service account's SYSTEM token: operational endpoints only."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.has_role(SYSTEM) and user.id is None)


class IsService:
    """One named service account (SYSTEM token), for a narrow machine-to-machine endpoint."""

    def __init__(self, name):
        self.name = name

    def __call__(self):  # DRF instantiates permission classes; this lets IsService("payments") be listed directly
        return self

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.has_role(SYSTEM) and user.id is None and user.service == self.name)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
