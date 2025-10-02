# Python Standard Library Imports
import os
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()


# Third-party Imports
from flask import Flask

# Local Imports


# Initialize the App
app = Flask(__name__)

# Set Secret Key for CSRF Protection
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')


# Register Blueprints

@app.route('/')
def index():
    return "pong", 200


if __name__ == '__main__':
    host = os.getenv('FLASK_HOST')
    port = os.getenv('FLASK_PORT')
    app.run(host=host, port=port, debug=True)
