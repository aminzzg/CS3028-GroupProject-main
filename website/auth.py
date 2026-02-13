from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from sqlalchemy import create_engine, text

auth = Blueprint('auth', __name__)


engine = create_engine(
    "mysql+pymysql://sql8816230:WKYhqaXPiu@sql8.freesqldatabase.com:3306/sql8816230"
)
@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT StaffID, Name, Username, Password, Position FROM Staff WHERE Username = :u AND Password = :p"),
                {"u": username, "p": password}  
            ).mappings().fetchone()

        if row:
            session['staff_id'] = row['StaffID']
            session['username'] = row['Username']
            session['name'] = row['Name']
            session['role'] = row['Position']

            # 🔹 Now all roles go directly to Notifications (Dashboard)
            return redirect(url_for('views.notifications'))

        else:
            flash("Invalid username or password.")

    return render_template('login.html')



@auth.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    flash("Logged out successfully.")
    return redirect(url_for('auth.login'))
