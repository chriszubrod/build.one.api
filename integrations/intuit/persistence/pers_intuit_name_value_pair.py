import pyodbc

from .. import database_pers


def create_name_value_pair(name, value, company_info_id):
    resp = {}
    sql = (
        '''
        INSERT INTO intuit.NameValue ([Name], [Value], CompanyInfoId)
        VALUES (?, ?, ?);
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, name, value, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Name Value has been successfully created.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Name Value has NOT been successfully created.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 500
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def read_name_value_pair_by_name_and_company_id(name, company_info_id):
    resp = {}
    sql = (
        '''
        SELECT *
        FROM intuit.NameValue
        WHERE [Name]=? AND CompanyInfoId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        crsr = cnxn.cursor()
        row = crsr.execute(sql, name, company_info_id).fetchone()
        if row:
            resp = {
                "message": row,
                "rowcount": len(row),
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Name Value was not found.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 500
        }
    finally:
        return resp


def update_value_by_name_and_company_id(name, value, company_info_id):
    resp = {}
    sql = (
        '''
        UPDATE intuit.NameValue
        SET [Value]=?
        WHERE [Name]=? AND CompanyInfoId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, value, name, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Name Value has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Name Value has NOT been successfully updated.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 500
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp
