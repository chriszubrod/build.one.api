# python standard library imports
import os

# third party imports
from flask import (
    Flask
)
from flask_wtf.csrf import CSRFProtect


# local imports
from modules.address import (
    api_address,
    web_address
)
from modules.attachment import (
    api_attachment,
    web_attachment
)
from modules.auth import (
    api_auth,
    web_auth
)
from modules.bill import (
    api_bill,
    web_bill
)
from modules.certificate import (
    api_certificate,
    web_certificate
)
from modules.certificate_type import (
    api_certificate_type,
    web_certificate_type
)
from modules.company import (
    api_company,
    web_company
)
from modules.contact import (
    api_contact,
    web_contact
)
from modules.cost_code import (
    api_cost_code,
    web_cost_code
)
from modules.customer import (
    api_customer,
    web_customer
)
from modules.dashboard import (
    api_dashboard,
    web_dashboard
)
from modules.module import (
    api_module,
    web_module
)
from modules.payment_term import (
    api_payment_term,
    web_payment_term
)
from modules.project import (
    api_project,
    web_project
)
from modules.user import (
    api_user,
    web_user
)
from modules.role import (
    api_role,
    web_role
)
from modules.sub_cost_code import (
    api_sub_cost_code,
    web_sub_cost_code
)
from modules.vendor import (
    api_vendor,
    web_vendor
)
from modules.vendor_type import (
    api_vendor_type,
    web_vendor_type
)


from utils.config_help import get_secrets, write_secrets, update_secrets

from integrations.ms.auth import api_ms_auth
from integrations.ms.web.picker.web_ms_picker import web_ms_picker_bp
from integrations.ms.drives import api_ms_drives



# initialize the app
app = Flask(__name__)

# Set secret key for CSRF protection
app.config['SECRET_KEY'] = '63e60b4b7f62c96222b738ad13fd918caa7f4fe712cfde86ca28662d38056d30'

# CSRF Setup
csrf = CSRFProtect(app)


# register blueprints
app.register_blueprint(api_address.api_address_bp)
app.register_blueprint(web_address.web_address_bp)

app.register_blueprint(api_attachment.api_attachment_bp)
app.register_blueprint(web_attachment.web_attachment_bp)

app.register_blueprint(api_auth.api_auth_bp)
app.register_blueprint(web_auth.web_auth_bp)

app.register_blueprint(api_bill.api_bill_bp)
app.register_blueprint(web_bill.web_bill_bp)

app.register_blueprint(api_certificate.api_certificate_bp)
app.register_blueprint(web_certificate.web_certificate_bp)

app.register_blueprint(api_certificate_type.api_certificate_type_bp)
app.register_blueprint(web_certificate_type.web_certificate_type_bp)

app.register_blueprint(api_company.api_company_bp)
app.register_blueprint(web_company.web_company_bp)

app.register_blueprint(api_contact.api_contact_bp)
app.register_blueprint(web_contact.web_contact_bp)

app.register_blueprint(api_cost_code.api_cost_code_bp)
app.register_blueprint(web_cost_code.web_cost_code_bp)

app.register_blueprint(api_customer.api_customer_bp)
app.register_blueprint(web_customer.web_customer_bp)

app.register_blueprint(api_dashboard.api_dashboard_bp)
app.register_blueprint(web_dashboard.web_dashboard_bp)

app.register_blueprint(api_module.api_module_bp)
app.register_blueprint(web_module.web_module_bp)

app.register_blueprint(api_payment_term.api_payment_term_bp)
app.register_blueprint(web_payment_term.web_payment_term_bp)

app.register_blueprint(api_project.api_project_bp)
app.register_blueprint(web_project.web_project_bp)

app.register_blueprint(api_user.api_user_bp)
app.register_blueprint(web_user.web_user_bp)

app.register_blueprint(api_role.api_role_bp)
app.register_blueprint(web_role.web_role_bp)

app.register_blueprint(api_sub_cost_code.api_sub_cost_code_bp)
app.register_blueprint(web_sub_cost_code.web_sub_cost_code_bp)

app.register_blueprint(api_vendor.api_vendor_bp)
app.register_blueprint(web_vendor.web_vendor_bp)

app.register_blueprint(api_vendor_type.api_vendor_type_bp)
app.register_blueprint(web_vendor_type.web_vendor_type_bp)



# integration blueprints
app.register_blueprint(api_ms_auth.api_ms_auth_bp)
app.register_blueprint(web_ms_picker_bp)
app.register_blueprint(api_ms_drives.api_ms_drives_bp)



@app.route('/')
def index():
    return "pong", 200
