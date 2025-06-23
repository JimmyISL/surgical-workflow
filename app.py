from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit
import pandas as pd
import json
from datetime import datetime
from config import Config
from models import db, WorkflowInstance, Task, WORKFLOW_STEPS, create_next_task

app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*")

def create_tables():
    """Create database tables"""
    with app.app_context():
        db.create_all()

# Routes
@app.route('/')
def index():
    """Main page - redirect to Kristin's dashboard for now"""
    return redirect(url_for('dashboard', user='kristin'))

@app.route('/postponed-workflows')
def postponed_workflows():
    """Show postponed workflows"""
    from datetime import date
    
    postponed = WorkflowInstance.query.filter_by(status='postponed').order_by(WorkflowInstance.created_date.desc()).all()
    
    postponed_data = []
    for workflow in postponed:
        # Get the last task to see postpone details
        last_task = Task.query.filter_by(workflow_id=workflow.id, status='postponed').first()
        
        workflow_info = {
            'id': workflow.id,
            'patient_name': workflow.patient_name,
            'patient_id': workflow.patient_id,
            'order_name': workflow.order_name,
            'order_desc': workflow.order_desc,
            'created_date': workflow.created_date,
            'postpone_reason': last_task.action_reason if last_task else '',
            'postpone_date': last_task.action_date if last_task else None,
            'postpone_until': last_task.postpone_until if last_task else None,
            'batch_id': workflow.batch_id
        }
        postponed_data.append(workflow_info)
    
    return render_template('postponed_workflows.html', 
                         postponed_workflows=postponed_data,
                         today=date.today())

@app.route('/task-history')
def task_history():
    """Show completed tasks history"""
    # Get all completed, cancelled, and postponed tasks, ordered by date (newest first)
    completed_tasks = Task.query.filter(
        Task.status.in_(['completed', 'cancelled', 'postponed'])
    ).order_by(Task.completed_date.desc(), Task.action_date.desc()).all()
    
    # Prepare task data with workflow info
    task_history = []
    for task in completed_tasks:
        workflow = task.workflow
        step_config = WORKFLOW_STEPS.get(task.step_name, {})
        
        task_info = {
            'id': task.id,
            'patient_name': workflow.patient_name,
            'patient_id': workflow.patient_id,
            'order_name': workflow.order_name,
            'order_desc': workflow.order_desc,
            'step_name': step_config.get('name', task.step_name),
            'assigned_to': task.assigned_to.title(),
            'completed_date': task.completed_date or task.action_date,
            'decision': task.decision,
            'notes': task.notes or task.action_reason,
            'appeal_round': task.appeal_round,
            'batch_id': workflow.batch_id,
            'status': task.status  # Include task status for display
        }
        task_history.append(task_info)
    
    return render_template('task_history.html', task_history=task_history)

@app.route('/dashboard/<user>')
def dashboard(user):
    """Dashboard for Kristin or Sharon"""
    if user not in ['kristin', 'sharon']:
        return "Invalid user", 400
    
    # Get tasks grouped by step (only pending tasks from active workflows)
    tasks = Task.query.join(WorkflowInstance).filter(
        Task.assigned_to == user,
        Task.status == 'pending',
        WorkflowInstance.status == 'active'
    ).all()
    
    # Group tasks by step
    grouped_tasks = {}
    for task in tasks:
        step_config = WORKFLOW_STEPS.get(task.step_name, {})
        step_display_name = step_config.get('name', task.step_name)
        
        if step_display_name not in grouped_tasks:
            grouped_tasks[step_display_name] = []
        
        # Add workflow info to task
        task_info = {
            'id': task.id,
            'patient_name': task.workflow.patient_name,
            'order_name': task.workflow.order_name,
            'order_desc': task.workflow.order_desc,
            'created_date': task.created_date.strftime('%m/%d/%Y'),
            'step_name': task.step_name,
            'requires_decision': step_config.get('requires_decision', False),
            'appeal_round': task.appeal_round
        }
        grouped_tasks[step_display_name].append(task_info)
    
    return render_template('dashboard.html', 
                         user=user.title(), 
                         grouped_tasks=grouped_tasks)

@app.route('/task/<int:task_id>')
def task_detail(task_id):
    """Show detailed view of a task"""
    task = Task.query.get_or_404(task_id)
    workflow = task.workflow
    csv_data = workflow.get_csv_data()
    
    step_config = WORKFLOW_STEPS.get(task.step_name, {})
    
    return render_template('task_detail.html',
                         task=task,
                         workflow=workflow,
                         csv_data=csv_data,
                         step_config=step_config)

@app.route('/cancel_workflow/<int:task_id>', methods=['POST'])
def cancel_workflow(task_id):
    """Cancel entire workflow for a patient"""
    task = Task.query.get_or_404(task_id)
    workflow = task.workflow
    
    data = request.get_json()
    reason = data.get('reason', '')
    
    # Mark workflow as cancelled
    workflow.status = 'cancelled'
    
    # Cancel all pending tasks for this workflow
    pending_tasks = Task.query.filter_by(workflow_id=workflow.id, status='pending').all()
    for pending_task in pending_tasks:
        pending_task.status = 'cancelled'
        pending_task.action_reason = reason
        pending_task.action_date = datetime.utcnow()
    
    # Mark current task as cancelled if it's not already completed
    if task.status == 'pending':
        task.status = 'cancelled'
        task.action_reason = reason
        task.action_date = datetime.utcnow()
    
    db.session.commit()
    
    # Emit real-time update
    socketio.emit('workflow_cancelled', {
        'workflow_id': workflow.id,
        'patient_name': workflow.patient_name
    })
    
    return jsonify({
        'success': True,
        'message': f'Workflow cancelled for {workflow.patient_name}'
    })

@app.route('/postpone_workflow/<int:task_id>', methods=['POST'])
def postpone_workflow(task_id):
    """Postpone entire workflow for a patient"""
    task = Task.query.get_or_404(task_id)
    workflow = task.workflow
    
    data = request.get_json()
    reason = data.get('reason', '')
    postpone_until_str = data.get('postpone_until', '')
    
    try:
        # Parse postpone date
        postpone_until = datetime.strptime(postpone_until_str, '%Y-%m-%d') if postpone_until_str else None
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    # Mark workflow as postponed
    workflow.status = 'postponed'
    
    # Postpone all pending tasks for this workflow
    pending_tasks = Task.query.filter_by(workflow_id=workflow.id, status='pending').all()
    for pending_task in pending_tasks:
        pending_task.status = 'postponed'
        pending_task.action_reason = reason
        pending_task.action_date = datetime.utcnow()
        pending_task.postpone_until = postpone_until
    
    # Mark current task as postponed if it's not already completed
    if task.status == 'pending':
        task.status = 'postponed'
        task.action_reason = reason
        task.action_date = datetime.utcnow()
        task.postpone_until = postpone_until
    
    db.session.commit()
    
    # Emit real-time update
    socketio.emit('workflow_postponed', {
        'workflow_id': workflow.id,
        'patient_name': workflow.patient_name,
        'postpone_until': postpone_until_str
    })
    
    return jsonify({
        'success': True,
        'message': f'Workflow postponed for {workflow.patient_name}'
    })

@app.route('/reactivate_workflow/<int:workflow_id>', methods=['POST'])
def reactivate_workflow(workflow_id):
    """Reactivate a postponed workflow - starts from step 1"""
    workflow = WorkflowInstance.query.get_or_404(workflow_id)
    
    if workflow.status != 'postponed':
        return jsonify({'error': 'Workflow is not postponed'}), 400
    
    # Mark workflow as active
    workflow.status = 'active'
    workflow.current_step = 'prior_authorization'
    
    # Cancel all existing tasks for this workflow
    existing_tasks = Task.query.filter_by(workflow_id=workflow.id).all()
    for existing_task in existing_tasks:
        if existing_task.status != 'completed':
            existing_task.status = 'cancelled'
            existing_task.action_reason = 'Workflow reactivated - starting fresh'
            existing_task.action_date = datetime.utcnow()
    
    # Create new first task (Prior Authorization)
    new_task = Task(
        workflow_id=workflow.id,
        step_name='prior_authorization',
        assigned_to='kristin'
    )
    
    db.session.add(new_task)
    db.session.commit()
    
    # Emit real-time update
    socketio.emit('workflow_reactivated', {
        'workflow_id': workflow.id,
        'patient_name': workflow.patient_name
    })
    
    return jsonify({
        'success': True,
        'message': f'Workflow reactivated for {workflow.patient_name}'
    })

@app.route('/complete_task/<int:task_id>', methods=['POST'])
def complete_task(task_id):
    """Mark a task as complete and create next task"""
    task = Task.query.get_or_404(task_id)
    workflow = task.workflow
    
    data = request.get_json()
    notes = data.get('notes', '')
    decision = data.get('decision')  # 'approved' or 'denied' for auth tasks
    
    # Update current task
    task.status = 'completed'
    task.completed_date = datetime.utcnow()
    task.notes = notes
    task.decision = decision
    
    # Create next task based on workflow logic
    next_task = create_next_task(workflow, task, decision)
    
    db.session.commit()
    
    # Emit real-time update to all connected clients
    socketio.emit('task_updated', {
        'task_id': task_id,
        'status': 'completed',
        'next_task_created': next_task.id if next_task else None
    })
    
    return jsonify({
        'success': True,
        'message': 'Task completed successfully',
        'next_task_id': next_task.id if next_task else None
    })

@app.route('/webhook/create-workflows', methods=['POST'])
def webhook_create_workflows():
    """Webhook endpoint for Zapier to create new workflows from CSV"""
    try:
        data = request.get_json()
        
        # Handle different input formats from Zapier
        if 'csv_content' in data:
            # Format 1: Full CSV content as string
            csv_content = data.get('csv_content')
            if isinstance(csv_content, str):
                import io
                df = pd.read_csv(io.StringIO(csv_content))
                csv_records = df.to_dict('records')
            else:
                csv_records = csv_content
        elif 'records' in data:
            # Format 2: Pre-parsed records from Zapier
            csv_records = data.get('records', [])
        else:
            # Format 3: Direct single record from Zapier
            # When Zapier sends one row at a time
            csv_records = [data]
        
        if not csv_records:
            return jsonify({'error': 'No data provided'}), 400
        
        # Filter for SURGERY entries only (case insensitive)
        surgery_records = []
        for record in csv_records:
            order_type = str(record.get('order_type_grp', '') or record.get('order type grp', '')).upper()
            if 'SURGERY' in order_type:
                surgery_records.append(record)
        
        if not surgery_records:
            return jsonify({
                'message': 'No SURGERY entries found in data',
                'total_records': len(csv_records),
                'surgery_records': 0
            }), 200
        
        # Create batch ID based on current timestamp
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        workflows_created = 0
        
        for record in surgery_records:
            # Handle different field name variations from Zapier
            patient_name = (record.get('patient_name') or 
                          record.get('patient name') or 
                          record.get('Patient Name') or 'Unknown')
            
            patient_id = (record.get('patient_id') or 
                         record.get('patientid') or 
                         record.get('Patient ID') or '')
            
            order_name = (record.get('order_name') or 
                         record.get('order name') or 
                         record.get('Order Name') or '')
            
            order_desc = (record.get('order_desc') or 
                         record.get('order desc') or 
                         record.get('Order Description') or '')
            
            # Create workflow instance
            workflow = WorkflowInstance(
                patient_name=patient_name,
                patient_id=str(patient_id),
                order_name=order_name,
                order_desc=order_desc,
                batch_id=batch_id
            )
            workflow.set_csv_data(record)
            
            db.session.add(workflow)
            db.session.flush()  # Get the ID
            
            # Create first task (Prior Authorization)
            first_task = Task(
                workflow_id=workflow.id,
                step_name='prior_authorization',
                assigned_to='kristin'
            )
            
            db.session.add(first_task)
            workflows_created += 1
        
        db.session.commit()
        
        # Emit real-time notification
        socketio.emit('new_workflows_created', {
            'count': workflows_created,
            'batch_id': batch_id
        })
        
        return jsonify({
            'success': True,
            'workflows_created': workflows_created,
            'batch_id': batch_id,
            'total_records_processed': len(csv_records),
            'surgery_records_found': len(surgery_records)
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Webhook error: {str(e)}")  # Log for debugging
        return jsonify({'error': str(e)}), 500

@app.route('/webhook/test', methods=['GET', 'POST'])
def webhook_test():
    """Test endpoint to verify webhook is working"""
    if request.method == 'GET':
        return jsonify({
            'message': 'Webhook endpoint is active',
            'timestamp': datetime.now().isoformat(),
            'status': 'ready'
        })
    else:
        # Echo back what was received for testing
        data = request.get_json() or {}
        return jsonify({
            'received': data,
            'timestamp': datetime.now().isoformat(),
            'status': 'received'
        })

# API endpoint to test CSV upload manually
@app.route('/test_csv_upload', methods=['POST'])
def test_csv_upload():
    """Test endpoint to manually upload CSV"""
    # Sample CSV data for testing
    sample_data = [
        {
            'order name': 'CUSTOMIZED WHEELCHAIR',
            'Created Date': '6/16/2025',
            'order type grp': 'SURGERY',
            'order desc': 'CUSTOMIZED WHEELCHAIR',
            'apprving provdr': 'dmoore411',
            'created provdr': 'gabraha1',
            'ordr provdr': 'dmoore411',
            'patient name': 'CAROLYN MILLER',
            'patientsex': 'F',
            'patientid': '32091'
        },
        {
            'order name': 'KNEE SURGERY',
            'Created Date': '6/16/2025',
            'order type grp': 'SURGERY',
            'order desc': 'KNEE REPLACEMENT SURGERY',
            'apprving provdr': 'jsmith123',
            'created provdr': 'mwilson456',
            'ordr provdr': 'jsmith123',
            'patient name': 'JOHN DOE',
            'patientsex': 'M',
            'patientid': '45672'
        }
    ]
    
    # Simulate the webhook call with test data
    try:
        # Filter for SURGERY entries only
        surgery_records = [
            record for record in sample_data 
            if record.get('order type grp', '').upper() == 'SURGERY'
        ]
        
        if not surgery_records:
            return jsonify({'message': 'No SURGERY entries found in CSV'}), 200
        
        # Create batch ID based on current timestamp
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        workflows_created = 0
        
        for record in surgery_records:
            # Create workflow instance
            workflow = WorkflowInstance(
                patient_name=record.get('patient name', 'Unknown'),
                patient_id=record.get('patientid', ''),
                order_name=record.get('order name', ''),
                order_desc=record.get('order desc', ''),
                batch_id=batch_id
            )
            workflow.set_csv_data(record)
            
            db.session.add(workflow)
            db.session.flush()  # Get the ID
            
            # Create first task (Prior Authorization)
            first_task = Task(
                workflow_id=workflow.id,
                step_name='prior_authorization',
                assigned_to='kristin'
            )
            
            db.session.add(first_task)
            workflows_created += 1
        
        db.session.commit()
        
        # Emit real-time notification
        socketio.emit('new_workflows_created', {
            'count': workflows_created,
            'batch_id': batch_id
        })
        
        return jsonify({
            'success': True,
            'workflows_created': workflows_created,
            'batch_id': batch_id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# SocketIO events for real-time updates
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('connected', {'message': 'Connected to surgical workflow system'})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    # Create tables when app starts
    create_tables()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)