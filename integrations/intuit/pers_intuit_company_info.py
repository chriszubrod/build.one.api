import pyodbc

from .. import database_pers


def create_company_info(realm_id, id, sync_token, company_name, supported_languages, country, fiscal_year_start_month, legal_name, company_start_date, employer_id, domain, sparse, created_datetime, last_update_datetime):
    resp = {}
    sql = (
        '''
        INSERT INTO intuit.CompanyInfo (RealmId, Id, SyncToken, CompanyName, SupportedLanguages, Country, FiscalYearStartMonth, LegalName, CompanyStartDate, EmployerId, Domain, Sparse, CreatedTime, LastUpdatedTime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, realm_id, id, sync_token, company_name, supported_languages, country, fiscal_year_start_month, legal_name, company_start_date, employer_id, domain, sparse, created_datetime, last_update_datetime).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Company Info has been successfully created.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Info has NOT been successfully created.",
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


def read_company_info_by_id(id):
    resp = {}
    sql = (
        '''
        SELECT RealmId, Id, SyncToken, CONVERT(datetime2, LastUpdatedTime, 1)
        FROM intuit.CompanyInfo
        WHERE [Id]=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        crsr = cnxn.cursor()
        row = crsr.execute(sql, id).fetchone()
        if row:
            resp = {
                "message": row,
                "rowcount": len(row),
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Info was not found.",
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


def update_company_info(realm_id, id, sync_token, company_name, supported_languages, country, fiscal_year_start_month, legal_name, company_start_date, employer_id, domain, sparse, created_datetime, last_update_datetime):
    resp = {}
    sql = (
        '''
        UPDATE intuit.CompanyInfo
        SET Id=?, SyncToken=?, CompanyName=?, SupportedLanguages=?, Country=?, FiscalYearStartMonth=?, LegalName=?, CompanyStartDate=?, EmployerId=?, Domain=?, Sparse=?, CreatedTime=?, LastUpdatedTime=?
        WHERE RealmId=?;
        '''
    )
    try:
        cnxn = database_pers.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, id, sync_token, company_name, supported_languages, country, fiscal_year_start_month, legal_name, company_start_date, employer_id, domain, sparse, created_datetime, last_update_datetime, realm_id).rowcount
        if count == 1:
            resp = {
                "message": "Intuit Company Info has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit Company Info has NOT been successfully updated.",
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
