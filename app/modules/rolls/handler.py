from flask import Blueprint

from app.web import stage

bp = Blueprint('rolls', __name__)


@bp.get('/api/rolls')
def inspect():
    return stage('rolls')
