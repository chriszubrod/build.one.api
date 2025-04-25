"""
Module for payment term web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_payment_term


payment_term_web_bp = Blueprint('payment_term_web', __name__)


@payment_term_web_bp.route('/payment-terms', methods=['GET'])
def list_payment_terms_route():
    """
    Returns the route for the payment terms page.
    """
    _payment_terms = []
    get_payment_terms_bus_response = bus_payment_term.get_payment_terms()
    if get_payment_terms_bus_response.success:
        _payment_terms = get_payment_terms_bus_response.data

    print(_payment_terms)
    return render_template('payment_term/list.html', payment_terms=_payment_terms)


@payment_term_web_bp.route('/payment-term/create', methods=['GET'])
def create_payment_term_route():
    """
    Returns the payment term create route for the application.
    """
    return render_template('payment_term/create.html')


@payment_term_web_bp.route('/payment-term/<payment_term_guid>', methods=['GET'])
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

    return render_template('payment_term/view.html', payment_term=_payment_term)


@payment_term_web_bp.route('/payment-term/<payment_term_guid>/edit', methods=['GET'])
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

    return render_template('payment_term/edit.html', payment_term=_payment_term)
