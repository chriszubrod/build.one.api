from flask import (
    jsonify,
    Flask,
    render_template
)
from agents import sample_agent

from blueprints.api import (
    api_document_classification
)

from blueprints.web import (
    web_bill,
    web_vendor,
    web_document
)

app = Flask(__name__)

app.register_blueprint(api_document_classification.api_document_classification_bp)

app.register_blueprint(web_bill.web_bill_bp)
app.register_blueprint(web_document.web_document_bp)
app.register_blueprint(web_vendor.web_vendor_bp)



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
