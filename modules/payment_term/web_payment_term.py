"""
Module for payment term web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.payment_term import bus_payment_term
from utils.auth_help import requires_auth


web_payment_term_bp = Blueprint('web_payment_term', __name__, template_folder='templates')


@web_payment_term_bp.route('/payment-terms', methods=['GET'])
@requires_auth()
def list_payment_terms_route():
    """
    Returns the route for the payment terms page.
    """
    _payment_terms = []
    get_payment_terms_bus_response = bus_payment_term.get_payment_terms()
    if get_payment_terms_bus_response.success:
        _payment_terms = get_payment_terms_bus_response.data

    print(_payment_terms)
    return render_template('payment_term_list.html', payment_terms=_payment_terms)


@web_payment_term_bp.route('/payment-term/create', methods=['GET'])
@requires_auth()
def create_payment_term_route():
    """
    Returns the payment term create route for the application.
    """
    return render_template('payment_term_create.html')


@web_payment_term_bp.route('/payment-term/<payment_term_guid>', methods=['GET'])
@requires_auth()
def view_payment_term_route(payment_term_guid):
    """
    Returns the payment term by guid route.
    """
    _payment_term = {}
    get_payment_term_bus_response = bus_payment_term.get_payment_term_by_guid(payment_term_guid)
    if get_payment_term_bus_response.success:
        _payment_term = get_payment_term_bus_response.data
    else:
        _payment_term = {}

    return render_template('payment_term_view.html', payment_term=_payment_term)


@web_payment_term_bp.route('/payment-term/<payment_term_guid>/edit', methods=['GET'])
@requires_auth()
def edit_payment_term_route(payment_term_guid):
    """
    Returns the payment term edit route.
    """
    _payment_term = {}
    get_payment_term_bus_response = bus_payment_term.get_payment_term_by_guid(payment_term_guid)
    if get_payment_term_bus_response.success:
        _payment_term = get_payment_term_bus_response.data
    else:
        _payment_term = {}

    return render_template('payment_term_edit.html', payment_term=_payment_term)
