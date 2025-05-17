"""
Module for dashboard API.
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, render_template, session

# local imports
from modules.module import bus_module
from utils.token_help import generate_token 


api_dashboard_bp = Blueprint('api_dashboard', __name__, url_prefix='/api')

