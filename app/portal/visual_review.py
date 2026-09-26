"""Colab browser check. Run explicitly against Django's isolated test database.

This module is intentionally outside test*.py discovery: the release installer
runs it explicitly after the functional suite, with Chromium installed.
"""
import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (Camp, Course, CourseInstructor, CourseSession, Instructor,
                     InstructorInventoryBalance, InstructorRole, InventoryItem, Registration, Student, Team)
from .inventory_models import WarehouseStockPolicy


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost','127.0.0.1','testserver'],
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
                             'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WebsiteBrowserReview(StaticLiveServerTestCase):
    def setUp(self):
        if not os.environ.get('IQARUS_UI_OUTPUT'):
            self.fail('The browser check requires an output folder supplied by the isolated installer.')
        self.output=Path(os.environ['IQARUS_UI_OUTPUT'])
        self.output.mkdir(parents=True,exist_ok=True)
        self.review_checks=[]
        self.review_failures=[]
        self.password='Disposable-Review-Account-2026!'
        self.admin=get_user_model().objects.create_user('visual-review-admin',password=self.password,is_staff=True,is_superuser=True)
        self.course=Course.objects.create(title_english='ASM · TCCC')
        self.camp=Camp.objects.create(name='Review Training Camp')
        self.session=CourseSession.objects.create(course=self.course,camp=self.camp,status='registration_open',registration_published=True,
            start_date=timezone.localdate(),end_date=timezone.localdate(),capacity=20,poc_name='Camp Contact',poc_contact_number='+971 50 123 4567',poc_location_url='https://example.com/map')
        team=Team.objects.create(name='Team 1')
        role=InstructorRole.objects.create(name='Instructor')
        for index,name in enumerate(('Review Instructor One','Review Instructor With A Longer Full Name')):
            instructor=Instructor.objects.create(name_english=name,email=f'instructor{index}@example.com',phone='+971 50 123 4567',team=team)
            instructor.roles.add(role)
            CourseInstructor.objects.create(session=self.session,instructor=instructor)
        self.item=InventoryItem.objects.create(name='Compressed gauze',unit='each',quantity_on_hand=100)
        WarehouseStockPolicy.objects.create(item=self.item,minimum_quantity=20,maximum_quantity=100,lead_time_days=14)
        for instructor in Instructor.objects.all():
            InstructorInventoryBalance.objects.create(instructor=instructor,item=self.item,quantity_on_hand=10)
        student=Student.objects.create(name_english='Review Student Full Name',name_arabic='اسم الطالب الكامل',eid='784199012345671',email='student@example.com',identity_status='verified')
        self.student=student
        self.registration=Registration.objects.create(student=student,requested_session=self.session,submitted_name_english=student.name_english,
            submitted_name_arabic=student.name_arabic,eid_raw=student.eid,eid_normalized=student.eid,email_raw=student.email,
            selected_for_roster=True,day1_attended=True,day2_attended=True,assessment_status='passed')
        # A one-instructor row tests equal heights beside a two-instructor row.
        second=CourseSession.objects.create(course=self.course,camp=self.camp,status='completed',start_date=date(2026,1,1),end_date=date(2026,1,2))
        CourseInstructor.objects.create(session=second,instructor=Instructor.objects.first())

    @contextmanager
    def review_check(self, label, page=None):
        """Report independent browser failures together, keeping every gate."""
        with self.subTest(check=label):
            try:
                yield
            except Exception as error:
                self.review_failures.append({'check':label,'error':str(error)})
                if page is not None and not page.is_closed():
                    try:
                        page.screenshot(path=str(self.output/f'failure-{len(self.review_failures)}.png'),full_page=True)
                    except Exception:
                        pass  # Preserve the original assertion if capture fails.
                raise
            else:
                self.review_checks.append(label)

    def test_pages_interactions_and_rendered_dimensions(self):
        from playwright.sync_api import expect, sync_playwright
        console_errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            try:
                page=browser.new_page(viewport={'width':1366,'height':900},device_scale_factor=1)
                page.on('pageerror',lambda error: console_errors.append(str(error)))
                response=page.goto(self.live_server_url+reverse('login'))
                self.assertEqual(response.status,200)
                page.locator('input[name=username]').fill('visual-review-admin')
                page.locator('input[name=password]').fill(self.password)
                page.locator('button[type=submit]').click()
                page.wait_for_url('**/my-courses/')
                with self.review_check('Course opening, required duplicate review, roster save and selected print',page):
                    self.assertEqual(page.locator('.job-card-open').count(),2)
                    page.locator('.job-card-open').first.click()
                    page.locator('#roster-form').wait_for()
                    self.assertTrue(page.get_by_role('heading',name='Material Used',exact=True).is_visible())
                    self.assertEqual(page.locator('.inventory-person').count(),2)
                    # This fixture is an existing master student. Follow the same
                    # explicit review step required of instructors; never bypass it.
                    page.locator('input[name^=name_english_]').fill('Review Student Corrected Name')
                    with page.expect_navigation(wait_until='networkidle'):
                        page.locator('button[form="roster-form"]').first.click()
                    messages=page.locator('[data-toast-message]').all_text_contents()
                    self.assertTrue(any('Choose Reviewed' in text for text in messages),str(messages))
                    expect(page.locator('input[name^=name_english_]')).to_have_value(self.registration.submitted_name_english)

                    page.locator('input[name^=name_english_]').fill('Review Student Corrected Name')
                    page.locator(f'input[name="duplicate_decision_{self.registration.public_id}"][value="reviewed_allow"]').check()
                    with page.expect_navigation(wait_until='networkidle'):
                        page.locator('button[form="roster-form"]').first.click()
                    messages=page.locator('[data-toast-message]').all_text_contents()
                    self.assertTrue(any('Course roster saved for 1 selected students.' in text for text in messages),str(messages))
                    expect(page.locator('input[name^=name_english_]')).to_have_value('REVIEW STUDENT CORRECTED NAME')

                    page.goto(self.live_server_url+reverse('students'))
                    page.locator('.row-checkbox').first.check()
                    self.assertTrue(page.locator('#print-selected').is_enabled())
                    with page.expect_popup() as popup:
                        page.locator('#print-selected').click()
                    print_page=popup.value;print_page.wait_for_load_state()
                    try:
                        expect(print_page.locator('tbody .student-name')).to_have_text('REVIEW STUDENT CORRECTED NAME')
                    finally:
                        print_page.close()

                with self.review_check('Staff compact view and Edit Staff controls',page):
                    page.goto(self.live_server_url+reverse('staff'))
                    row=page.locator('tbody tr[data-staff-editable]').first
                    self.assertFalse(row.locator('input[name=name]').is_visible())
                    page.locator('[data-edit-staff]').click()
                    self.assertTrue(row.locator('input[name=name]').is_visible())
                    page.locator('[data-edit-staff]').click()
                    self.assertFalse(row.locator('input[name=name]').is_visible())

                with self.review_check('Inventory line controls and quantity total',page):
                    page.goto(self.live_server_url+reverse('inventory'))
                    self.assertEqual(page.locator('[data-issue-lines] tr').count(),1)
                    page.locator('[data-add-issue-line]').click()
                    self.assertEqual(page.locator('[data-issue-lines] tr').count(),2)
                    page.locator('[data-remove-issue-line]').last.click()
                    page.locator('select[name=line_item]').select_option(str(self.item.pk))
                    page.locator('input[name=line_quantity]').fill('3')
                    self.assertEqual(page.locator('[data-issue-total]').inner_text(),'3 each')

                with self.review_check('Excel report download',page):
                    page.goto(self.live_server_url+reverse('reports')+'?report=course_all')
                    with page.expect_download() as info:
                        page.locator('button[name=export]').click()
                    download=info.value
                    self.assertTrue(download.suggested_filename.endswith('.xlsx'))
                    self.assertIsNone(download.failure())

                with self.review_check('J35 assignment editor and spreadsheet colours',page):
                    page.goto(self.live_server_url+reverse('dashboard'))
                    page.locator('[data-cell-details]').first.click()
                    self.assertTrue(page.locator('.j35-cell-dialog').is_visible())
                    page.locator('[data-j35-color]').nth(1).click()
                    self.assertNotEqual(page.locator('.j35-cell-dialog input[name=color]').input_value(),'#ffffff')
                    page.locator('.j35-cell-dialog [data-close-dialog]').first.click()

                pages=[('dashboard',reverse('dashboard')),('my-courses',reverse('my_courses')),
                       ('course',reverse('instructor_course_workspace',args=[self.session.public_id])),
                       ('students',reverse('students')),('directory',reverse('courses_sessions')),
                       ('staff',reverse('staff')),('inventory',reverse('inventory')),('reports',reverse('reports'))]
                for width in (1366,1920):
                    page.set_viewport_size({'width':width,'height':1080 if width==1920 else 900})
                    for name,url in pages:
                        with self.review_check(f'{name} layout at {width}px',page):
                            response=page.goto(self.live_server_url+url,wait_until='networkidle')
                            self.assertEqual(response.status,200,f'{name} at {width}px')
                            page.evaluate('document.fonts.ready')
                            self.assertEqual(page.locator('meta[name="iqarus-release"]').get_attribute('content'),'corrections-20260923')
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'),f'Page overflow: {name} at {width}px')
                            broken=page.locator('img').evaluate_all('(images) => images.filter(i => !i.complete || i.naturalWidth === 0).length')
                            self.assertEqual(broken,0,f'Broken logo/image: {name}')
                            if name=='directory':
                                heights=page.locator('tr.course-directory-row').evaluate_all('(rows) => rows.map(r => r.getBoundingClientRect().height)')
                                self.assertLessEqual(max(heights)-min(heights),2)
                                overlap=page.locator('.directory-badge').evaluate_all('(badges) => badges.some(b => {const p=b.closest("td");if(!p)return false;const a=b.getBoundingClientRect(),c=p.getBoundingClientRect();return a.right>c.right+1 || a.left<c.left-1;})')
                                self.assertFalse(overlap,'Course status overlaps another column.')
                            page.screenshot(path=str(self.output/f'{name}-{width}.png'),full_page=True)
                with self.review_check('No browser script errors',page):
                    self.assertEqual(console_errors,[],str(console_errors))
            except Exception as error:
                if 'page' in locals() and not page.is_closed():
                    page.screenshot(path=str(self.output/'failure.png'),full_page=True)
                    (self.output/'failure.txt').write_text(str(error))
                raise
            finally:
                browser.close()
        # Playwright has closed its event loop; database assertions run in the
        # ordinary synchronous Django test context, with async protection kept on.
        with self.review_check('Saved correction persists in the roster and sole master record'):
            self.registration.refresh_from_db()
            self.student.refresh_from_db()
            self.assertEqual(self.registration.submitted_name_english,'REVIEW STUDENT CORRECTED NAME')
            self.assertEqual(self.student.name_english,'REVIEW STUDENT CORRECTED NAME')
            self.assertEqual(self.registration.duplicate_review_status,'reviewed_allow')
        (self.output/'browser-check.json').write_text(json.dumps({'automated_checks':'failed' if self.review_failures else 'passed','checks':self.review_checks,'failures':self.review_failures,
            'visual_comparison':'Screenshots captured for review. Human comparison with reference images is still required.'},indent=2))
