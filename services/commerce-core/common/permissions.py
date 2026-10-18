from rest_framework.permissions import BasePermission

from .auth import CUSTOMER, SHOP_OWNER


class IsCustomer(BasePermission):
    """Any signed-in shopper. Shop owners can also shop."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and (user.has_role(CUSTOMER) or user.has_role(SHOP_OWNER)))


class IsShopOwner(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.has_role(SHOP_OWNER))
