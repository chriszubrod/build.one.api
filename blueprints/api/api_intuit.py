
@app.route('/intuit/companyinfo', methods=['GET'])
def process_intuit_company_info():
    bus_intuit_company_info_resp = bus_intuit_company_info.run_company_info_process()
    return {
        "message": bus_intuit_company_info_resp.get('message'),
        "rowcount": bus_intuit_company_info_resp.get('rowcount'),
        "status_code": bus_intuit_company_info_resp.get('status_code')
    }


@app.route('/intuit/customer', methods=['GET'])
def process_intuit_customer():
    '''
    '''

    # call run_customer_process in business layer
    bus_intuit_customer_resp = bus_intuit_customer.run_customer_process()

    # return json of message, rowcount and status_code from response.
    # flask will return dict as json format.
    return {
        "message": bus_intuit_customer_resp.get('message'),
        "rowcount": bus_intuit_customer_resp.get('rowcount'),
        "status_code": bus_intuit_customer_resp.get('status_code')
    }


@app.route('/intuit/vendor', methods=['GET'])
def process_intuit_vendor():
    '''Endpoint for Intuit Vendor.

    Calls vendor process in business layer, and returns process response.

    Args:
        None

    Returns:
        A json object mapping keys to the corresponding message, rowcount of records effected, and
        status code of the response. For example:

        {
         "message": "Test message.",
         "rowcount": 1,
         "status_code": 201
        }

    Raises:
        None
    '''

    # call run_vendor_process in business layer
    bus_intuit_vendor_resp = bus_intuit_vendor.run_vendor_process()

    # return json of message, rowcount and status_code from response.
    # flask will return dict as json format.
    return {
        "message": bus_intuit_vendor_resp.get('message'),
        "rowcount": bus_intuit_vendor_resp.get('rowcount'),
        "status_code": bus_intuit_vendor_resp.get('status_code')
    }


@app.route('/intuit/item', methods=['GET'])
def process_intuit_item():
    '''Endpoint for Intuit Item.

    Calls item process in business layer, and returns process response.

    Args:
        None

    Returns:
        A json object mapping keys to the corresponding message, rowcount of records effected, and
        status code of the response. For example:

        {
         "message": "Test message.",
         "rowcount": 1,
         "status_code": 201
        }

    Raises:
        None
    '''

    # call run_item_process in business layer
    bus_intuit_item_resp = bus_intuit_item.run_item_process()

    # return json of message, rowcount and status_code from response.
    # flask will return dict as json format.
    return {
        "message": bus_intuit_item_resp.get('message'),
        "rowcount": bus_intuit_item_resp.get('rowcount'),
        "status_code": bus_intuit_item_resp.get('status_code')
    }


@app.route('/api/post/intuit/bill', methods=['POST'])
def api_post_intuit_bill_route():
    post_intuit_bill(request)


def post_intuit_bill(_request):
    '''
    '''
    if not _request.is_json:
        return jsonify({'message': 'Request must be in JSON format.'}), 400

    try:
        form_data = _request.get_json()

        doc_number = form_data.get('number', '')
        txn_date = form_data.get('date', '')
        vendor_ref_value = form_data.get('vendor', '')
        entry_line_items = form_data.get('lineItems', [])

        entry_type_guid = form_data.get('entry_type', '')

        if not vendor_ref_value:
            return jsonify({'message': 'Missing Vendor.'}), 200

        if not doc_number:
            return jsonify({'message': 'Missing Entry Number.'}), 200

        if not txn_date:
            return jsonify({'message': 'Missing Entry Date.'}), 200

        if entry_line_items == []:
            return jsonify({'message': 'Missing Line Items.'}), 200

        line_items = []
        for item in entry_line_items:
            description = item.get('description', '')
            amount = float(item.get('amount', 0))
            customer_ref_value = item.get('project', '')
            is_billable = item.get('isBillable', '')
            item_ref_value = item.get('subCostCode', '')
            unit_price = float(item.get('rate', 0))
            qty = int(item.get('units', 0))

            if not item_ref_value:
                return jsonify({'message': 'Missing Sub Cost Code.'}), 200

            if not customer_ref_value:
                return jsonify({'message': 'Missing Project.'}), 200

            line_items.append(
                {
                    'description': description,
                    'amount': amount,
                    'customer_ref_value': customer_ref_value,
                    'is_billable': is_billable,
                    'item_ref_value': item_ref_value,
                    'unit_price': unit_price,
                    'qty': qty
                }
            )

        date_obj = datetime.strptime(txn_date, "%m/%d/%Y")
        formatted_date_str = date_obj.strftime("%Y-%m-%d")
        txn_date = formatted_date_str

        bill = {
            'doc_number': doc_number,
            'txn_date': txn_date,
            'vendor_ref_value': vendor_ref_value,
            'line_items': line_items
        }
        create_a_bill_process_response = bus_intuit_bill.create_a_bill_process(bill)

        if create_a_bill_process_response.get('status_code') == 401:
            return jsonify({'message': create_a_bill_process_response}), 401

        return jsonify({'message': create_a_bill_process_response}), 201

    except ValueError as ve:
        return jsonify({'message': str(ve)}), 500
    except TypeError as te:
        return jsonify({'message': str(te)}), 500

