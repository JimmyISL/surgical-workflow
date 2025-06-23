from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

class WorkflowInstance(db.Model):
    """Represents one patient's workflow from start to finish"""
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(100), nullable=False)
    patient_id = db.Column(db.String(50), nullable=False)
    order_name = db.Column(db.String(200), nullable=False)
    order_desc = db.Column(db.String(500))
    batch_id = db.Column(db.String(50), nullable=False)  # From CSV upload date
    current_step = db.Column(db.String(50), default='prior_authorization')
    status = db.Column(db.String(20), default='active')  # active, completed, cancelled, postponed
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    csv_data = db.Column(db.Text)  # Store full CSV row as JSON
    
    # Relationship to tasks
    tasks = db.relationship('Task', backref='workflow', lazy=True, cascade='all, delete-orphan')
    
    def get_csv_data(self):
        """Return CSV data as dictionary"""
        if self.csv_data:
            return json.loads(self.csv_data)
        return {}
    
    def set_csv_data(self, data_dict):
        """Store CSV data as JSON string"""
        self.csv_data = json.dumps(data_dict)

class Task(db.Model):
    """Individual tasks within a workflow"""
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow_instance.id'), nullable=False)
    step_name = db.Column(db.String(50), nullable=False)
    assigned_to = db.Column(db.String(50), nullable=False)  # 'kristin' or 'sharon'
    status = db.Column(db.String(20), default='pending')  # pending, in_progress, completed, cancelled, postponed
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    completed_date = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    decision = db.Column(db.String(20))  # For authorization: 'approved', 'denied'
    
    # For tracking cancellation/postponement
    action_reason = db.Column(db.Text)  # Reason for cancel/postpone
    action_date = db.Column(db.DateTime)  # When cancelled/postponed
    postpone_until = db.Column(db.DateTime)  # When to reactivate (for postponed tasks)
    
    # For appeal tracking
    appeal_round = db.Column(db.Integer, default=0)  # 0 = initial, 1+ = appeal rounds

# Define workflow steps
WORKFLOW_STEPS = {
    'prior_authorization': {
        'name': 'Prior Authorization',
        'assigned_to': 'kristin',
        'next_step': 'insurance_submission',
        'requires_decision': False
    },
    'insurance_submission': {
        'name': 'Insurance Submission',
        'assigned_to': 'kristin',
        'next_step': 'authorization_result',
        'requires_decision': False
    },
    'authorization_result': {
        'name': 'Authorization Result',
        'assigned_to': 'kristin',
        'next_step': None,  # Depends on decision
        'requires_decision': True,
        'approved_next': 'scheduling',
        'denied_next': 'appeal'
    },
    'appeal': {
        'name': 'Appeal',
        'assigned_to': 'kristin',
        'next_step': 'authorization_result',  # Goes back to auth result
        'requires_decision': False
    },
    'scheduling': {
        'name': 'Scheduling',
        'assigned_to': 'sharon',
        'next_step': 'site_approval',
        'requires_decision': False
    },
    'site_approval': {
        'name': 'Site Approval',
        'assigned_to': 'sharon',
        'next_step': 'surgery_scheduled',
        'requires_decision': False
    },
    'surgery_scheduled': {
        'name': 'Surgery Scheduled',
        'assigned_to': 'sharon',
        'next_step': None,  # Final step
        'requires_decision': False
    }
}

def create_next_task(workflow_instance, current_task, decision=None):
    """Create the next task in the workflow based on current task and decision"""
    current_step = WORKFLOW_STEPS.get(current_task.step_name)
    
    if not current_step:
        return None
    
    next_step_name = None
    
    # Handle decision-based routing
    if current_step.get('requires_decision') and decision:
        if decision == 'approved':
            next_step_name = current_step.get('approved_next')
            workflow_instance.current_step = next_step_name
        elif decision == 'denied':
            next_step_name = current_step.get('denied_next')
            # For appeals, increment the round
            appeal_round = current_task.appeal_round + 1 if current_task.step_name == 'authorization_result' else 0
    else:
        next_step_name = current_step.get('next_step')
        workflow_instance.current_step = next_step_name
    
    if not next_step_name:
        # Workflow completed
        workflow_instance.status = 'completed'
        return None
    
    next_step_config = WORKFLOW_STEPS.get(next_step_name)
    if not next_step_config:
        return None
    
    # Create the next task
    next_task = Task(
        workflow_id=workflow_instance.id,
        step_name=next_step_name,
        assigned_to=next_step_config['assigned_to'],
        appeal_round=appeal_round if next_step_name == 'appeal' else 0
    )
    
    db.session.add(next_task)
    return next_task