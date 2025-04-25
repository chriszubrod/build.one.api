
@app.route('/dashboard', methods=['GET'])
#@tm.token_verification
def get_dashboard_route():
    """
    Returns the dashboard route for the application.
    """
    # Generate a token.
    token_manager = tm.TokenManager(app)
    token = token_manager.generate_token()

    # Store the token in the session.
    session['timestamp'] = datetime.now().isoformat()
    session['token'] = token

    get_modules_response = bus_buildone_module.get_modules()
    if get_modules_response.success:
        _modules = get_modules_response.data
    else:
        _modules = []

    return render_template('dashboard.html', modules=_modules)



