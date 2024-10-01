from flask import Flask, render_template, request, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import google.generativeai as palm
import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
import re
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import openai
import json
from flask_weasyprint import HTML, render_pdf
from weasyprint import CSS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'alpha-beta-gamma'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
openai.api_key = os.getenv('OPENAI_API_KEY')

# Markdown instance for code block rendering
md = markdown.Markdown(extensions=[FencedCodeExtension()])

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True)
    email = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(80))
    courses = db.relationship('Course', backref='user', lazy=True)
    date_joined = db.Column(db.DateTime, default=datetime.now)

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)

# User loading for login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route("/quiz_interface")
def quiz_interface():
    return render_template("home.html")

@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    if request.method == "POST":
        language = request.form["language"]
        questions = request.form["ques"]
        choices = request.form["choices"]

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"Prepare a quiz for {language} with {questions} questions and {choices} choices each. Return the output as a JSON object."
                }
            ],
            temperature=0.7,
        )

        quiz_content = json.loads(response['choices'][0]['message']['content'])
        session['response'] = quiz_content
        return render_template("quiz.html", quiz_content=quiz_content)

    if request.method == "GET":
        score = 0
        actual_answers = []
        given_answers = list(request.args.values()) or []
        res = session.get('response', None)

        for answer in res["questions"]:
            actual_answers.append(answer["answer"])

        if given_answers:
            for i in range(len(actual_answers)):
                if actual_answers[i] == given_answers[i]:
                    score += 1

        return render_template("score.html", actual_answers=actual_answers, given_answers=given_answers, score=score)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_authenticated:
        return render_template('dashboard.html', user=current_user)
    return redirect(url_for('login'))

@app.route('/')
def home():
    if current_user.is_authenticated:
        saved_courses = Course.query.filter_by(user_id=current_user.id).all()
        recommended_courses = generate_recommendations(saved_courses)
        return render_template('app.html', saved_courses=saved_courses, recommended_courses=recommended_courses, user=current_user)
    return redirect(url_for('login'))

@app.route('/course', methods=['GET', 'POST'])
@login_required
def course():
    if request.method == 'POST':
        course_name = request.form['course_name']
        completions = generate_text(course_name)
        rendered = render_template('courses/course1.html', completions=completions, course_name=course_name)
        new_course = Course(course_name=course_name, content=rendered, user_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        return rendered
    return render_template('courses/course1.html')

@app.route('/module/<course_name>/<module_name>', methods=['GET'])
def module(course_name, module_name):
    content = generate_module_content(course_name, module_name)
    if not content:
        return "<p>Module not found</p>"
    html = render_template('module.html', content=content)

    if 'download' in request.args:
        a3_css = CSS(string='@page {size: A3; margin: 1cm;}')
        return render_pdf(HTML(string=html), stylesheets=[a3_css])

    return html

# Helper functions
def markdown_to_list(markdown_string):
    lines = markdown_string.split('\n')
    return [re.sub(r'\* ', '', line) for line in lines if line.startswith('* ')]

def generate_text(course):
    palm.configure(api_key=os.getenv("PALM_API_KEY"))
    models = [m for m in palm.list_models() if 'generateText' in m.supported_generation_methods]
    model = models[0].name

    prompts = {
        'approach': f"Describe the learning approach for {course} for undergrad students. Provide points and expected learning outcomes.",
        'modules': f"List modules for the course {course} with brief descriptions."
    }

    completions = {}
    for key, prompt in prompts.items():
        completion = palm.generate_text(model=model, prompt=prompt, temperature=0.1, max_output_tokens=5000)
        if key == 'modules':
            markdown_string = completion.result.replace('•', '*') if completion.result else ""
            completions[key] = markdown_to_list(markdown_string) if markdown_string else []
        else:
            completions[key] = markdown.markdown(completion.result) if completion.result else ""
    return completions

def generate_module_content(course_name, module_name):
    palm.configure(api_key=os.getenv("PALM_API_KEY"))
    models = [m for m in palm.list_models() if 'generateText' in m.supported_generation_methods]
    model = models[0].name

    module_prompt = f"Provide a detailed explanation of {module_name} from the course {course_name}. Use examples or analogies."
    module_completion = palm.generate_text(model=model, prompt=module_prompt, temperature=0.1, max_output_tokens=5000)
    return module_completion.result if module_completion.result else ""