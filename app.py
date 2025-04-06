from flask import (
    jsonify,
    Flask,
    render_template
)
from agents import sample_agent

app = Flask(__name__)


@app.route('/')
def index():
    return "Hello from Docker!"


@app.route('/agent')
def agent():
    try:
        response = sample_agent.run_agent()
        return render_template('index.html', result=response)
    except Exception as e:
        return jsonify(
            {"error": str(e)}
        ), 500

if __name__ == '__main__':
    app.run(debug=True)
