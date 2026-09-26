"""Reset only an installer-created copy; preserve the designated admin password."""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Sum
from . import models as m
from .course_services import enable_course_sequence_numbering


@transaction.atomic
def reset_copy(admin_username):
    User = get_user_model()
    admin = User.objects.get(username=admin_username)
    if not (admin.is_active and admin.is_staff and admin.is_superuser):
        raise ValueError('The preserved login must be an active superuser. No reset was made.')
    credential = admin.password
    names = list(m.Instructor.objects.order_by('pk').values_list('pk', 'name_english', 'name_arabic'))
    camps = list(m.Camp.objects.order_by('pk').values_list('pk', 'name'))
    inventory = list(m.InstructorInventoryBalance.objects.order_by('pk').values_list('pk', 'item_id', 'quantity_on_hand'))
    warehouse = list(m.InventoryItem.objects.order_by('pk').values_list('pk', 'quantity_on_hand'))
    removed = {}
    # Clear dependent evidence first; PROTECT constraints stay enabled throughout.
    order = ('CourseSessionProposal', 'StudentIdentityProposal', 'EntityAliasProposal',
             'DataIssue', 'SourceRecord', 'CourseRosterSnapshotItem', 'CourseRosterSnapshot',
             'StampedListArchive', 'TrainingRecord', 'Registration', 'InstructorAllocation',
             'J35TrainingNeed', 'J35GridCourseLink', 'J35GridCell', 'CourseInstructor',
             'CourseInstructorInventoryUsage', 'CourseInventoryUsage')
    for name in order:
        model = getattr(m, name)
        removed[name] = model.objects.count()
        model.objects.all().delete()
    removed['course_inventory_movements'] = m.InstructorInventoryMovement.objects.filter(session__isnull=False).count()
    m.InstructorInventoryMovement.objects.filter(session__isnull=False).delete()
    for name in ('CourseSession', 'Course', 'Student', 'SourceFile', 'Notification', 'ActivityLog'):
        model = getattr(m, name)
        removed[name] = model.objects.count()
        model.objects.all().delete()
    for staff in m.Instructor.objects.exclude(user_id=admin.pk):
        staff.roles.clear()
    m.Instructor.objects.update(team=None, supervisor=None, is_inventory_supervisor=False)
    m.Team.objects.update(leader=None)
    other_users = User.objects.exclude(pk=admin.pk)
    for user in other_users:
        user.groups.clear()
        user.user_permissions.clear()
    m.StaffPermissionOverride.objects.exclude(user_id=admin.pk).delete()
    other_users.update(is_staff=False, is_superuser=False)
    Session.objects.all().delete()
    m.LoginAttempt.objects.all().delete()
    enable_course_sequence_numbering(1)
    # User requested removal of equipment allocations too. Account for every
    # assigned unit as warehouse stock in this new copy; original ledger remains
    # with the untouched original website.
    equipment_returned = []
    for item in m.InventoryItem.objects.all():
        returned = m.InstructorInventoryBalance.objects.filter(item=item).aggregate(n=Sum('quantity_on_hand'))['n'] or 0
        old_quantity = item.quantity_on_hand
        item.quantity_on_hand += returned
        item.save(update_fields=['quantity_on_hand', 'updated_at'])
        equipment_returned.append({'item': item.name, 'warehouse_before': old_quantity,
                                   'returned_from_staff': returned, 'warehouse_after': item.quantity_on_hand})
    m.InventoryIssueLine.objects.all().delete()
    m.InventoryIssue.objects.all().delete()
    m.InstructorInventoryMovement.objects.all().delete()
    m.InstructorInventoryBalance.objects.update(quantity_on_hand=0, updated_by=admin,
        notes='Clean-start opening balance: previous allocation returned to warehouse.')
    admin.refresh_from_db()
    if admin.password != credential or not (admin.is_active and admin.is_staff and admin.is_superuser):
        raise RuntimeError('Administrator preservation check failed.')
    if names != list(m.Instructor.objects.order_by('pk').values_list('pk', 'name_english', 'name_arabic')) or camps != list(m.Camp.objects.order_by('pk').values_list('pk', 'name')):
        raise RuntimeError('Staff/camp preservation check failed.')
    if m.InstructorInventoryBalance.objects.exclude(quantity_on_hand=0).exists():
        raise RuntimeError('Equipment allocation reset check failed.')
    if sum(q for _, q in warehouse) + sum(q for _, _, q in inventory) != sum(m.InventoryItem.objects.values_list('quantity_on_hand', flat=True)):
        raise RuntimeError('Total equipment stock preservation check failed.')
    for name in ('Student', 'Course', 'CourseSession', 'Registration', 'TrainingRecord', 'InstructorAllocation'):
        if getattr(m, name).objects.exists():
            raise RuntimeError('Reset verification failed: ' + name)
    m.ActivityLog.objects.create(actor=admin, action='other', object_type='CleanStart',
        description='Created a clean copy with no courses or students; staff grants and allocations reset.', details={'removed': removed})
    return {'removed': removed, 'staff_kept': len(names), 'camps_kept': len(camps),
            'admin_username': admin.username, 'staff_accounts_kept': other_users.count(), 'equipment_returned': equipment_returned, 'next_course_number': 1}
