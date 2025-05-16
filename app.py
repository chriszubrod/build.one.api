from flask import (
    jsonify,
    Flask,
    render_template
)
#from agents import sample_agent


from modules.address import (
    api_address,
    web_address
)
from modules.bill import (
    api_bill,
    web_bill
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

app = Flask(__name__)

app.register_blueprint(api_address.api_address_bp)
app.register_blueprint(web_address.web_address_bp)

app.register_blueprint(api_bill.api_bill_bp)
app.register_blueprint(web_bill.web_bill_bp)

app.register_blueprint(api_company.api_company_bp)
app.register_blueprint(web_company.web_company_bp)

app.register_blueprint(api_contact.api_contact_bp)
app.register_blueprint(web_contact.web_contact_bp)

app.register_blueprint(api_cost_code.api_cost_code_bp)
app.register_blueprint(web_cost_code.web_cost_code_bp)

app.register_blueprint(api_customer.api_customer_bp)
app.register_blueprint(web_customer.web_customer_bp)

app.register_blueprint(api_project.api_project_bp)
app.register_blueprint(web_project.web_project_bp)

app.register_blueprint(api_user.api_user_bp)
app.register_blueprint(web_user.web_user_bp)

app.register_blueprint(api_role.api_role_bp)
app.register_blueprint(web_role.web_role_bp)

app.register_blueprint(api_sub_cost_code.api_sub_cost_code_bp)
app.register_blueprint(web_sub_cost_code.web_sub_cost_code_bp)




@app.route('/')
def index():
    return "Hello from Flask!"


@app.route('/agent')
def agent():
    try:
        response = sample_agent.run_agent()
        return render_template('index.html', result=response)
    except Exception as e:
        return jsonify(
            {"err": str(e)}
        ), 500

if __name__ == '__main__':
    app.run(debug=True)
