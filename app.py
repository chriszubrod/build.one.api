from flask import (
    jsonify,
    Flask,
    render_template
)
from agents import sample_agent


from modules.bill import (
    api_bill,
    web_bill
)
from modules.customer import (
    api_customer,
    web_customer
)
from modules.project import (
    api_project,
    web_project
)

app = Flask(__name__)

app.register_blueprint(api_bill.api_bill_bp)
app.register_blueprint(web_bill.web_bill_bp)

app.register_blueprint(api_customer.api_customer_bp)
app.register_blueprint(web_customer.web_customer_bp)

app.register_blueprint(api_project.api_project_bp)
app.register_blueprint(web_project.web_project_bp)


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
