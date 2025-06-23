import os
from app import app, db
from models import WorkflowInstance, Task

print("Testing new database configuration...")
print("Database URI:", app.config['SQLALCHEMY_DATABASE_URI'])

with app.app_context():
    # Create tables
    db.create_all()
    print("Tables created")
    
    # Check if file exists
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, "surgical_workflow.db")
    print(f"Looking for database at: {db_path}")
    
    if os.path.exists(db_path):
        print(f"✅ Database file exists!")
        print(f"File size: {os.path.getsize(db_path)} bytes")
    else:
        print("❌ Database file not found")
        
    # Test creating records
    try:
        # Create workflow
        workflow = WorkflowInstance(
            patient_name="Test Patient",
            patient_id="TEST123",
            order_name="Test Order",
            batch_id="test_batch"
        )
        db.session.add(workflow)
        db.session.flush()
        
        # Create task with new columns
        task = Task(
            workflow_id=workflow.id,
            step_name='prior_authorization',
            assigned_to='kristin',
            action_reason="Test reason"
        )
        db.session.add(task)
        db.session.commit()
        
        print("✅ Test records created successfully!")
        print("✅ New columns working!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()