import pyodbc

from .. import database_pers


def create_telephone_number(telephone_number, company_info_id):
    resp = {}
    sql = (
        '''
        INSERT INTO intuit.TelephoneNumber (FreeFormNumber, CompanyInfoId)
        VALUES (?, ?);
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, telephone_number, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Telephone Number has been successfully created.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Telephone Number has NOT been successfully created.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def read_telephone_number_by_company_id(company_id):
    resp = {}
    sql = (
        '''
        SELECT *
        FROM intuit.TelephoneNumber
        WHERE CompanyInfoId=?;        
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        crsr = cnxn.cursor()
        row = crsr.execute(sql, company_id).fetchone()
        if row:
            resp = {
                "message": row,
                "rowcount": len(row),
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Telephone Number was not found.",
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


def update_telephone_number_by_company_id(telephone_number, company_info_id):
    resp = {}
    sql = (
        '''
        UPDATE intuit.TelephoneNumber
        SET [Address]=?
        WHERE CompanyInfoId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, telephone_number, company_info_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Telephone Number has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Telephone Number has NOT been successfully updated.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = database_pers.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp
