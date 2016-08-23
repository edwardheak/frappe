# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals

import frappe

from frappe.model.db_schema import DbManager
from frappe.installer import get_root_connection
from frappe.database import Database
import os
from markdown2 import markdown
from bs4 import BeautifulSoup

def sync():
	# make table
	print 'Syncing help database...'
	help_db = HelpDatabase()
	help_db.make_database()
	help_db.connect()
	help_db.make_table()
	help_db.sync_pages()
	help_db.build_index()

@frappe.whitelist()
def get_help(text):
	return HelpDatabase().search(text)

@frappe.whitelist()
def get_help_content(path):
	return HelpDatabase().get_content(path)

class HelpDatabase(object):
	def __init__(self):
		self.help_db_name = '_frappe_help'

	def make_database(self):
		dbman = DbManager(get_root_connection())

		# make database
		if not self.help_db_name in dbman.get_database_list():
			try:
				dbman.create_user(self.help_db_name, self.help_db_name)
			except Exception, e:
				# user already exists
				if e.args[0] != 1396: raise
			dbman.create_database(self.help_db_name)
			dbman.grant_all_privileges(self.help_db_name, self.help_db_name)
			dbman.flush_privileges()

	def connect(self):
		self.db = Database(user=self.help_db_name, password=self.help_db_name)

	def make_table(self):
		if not 'help' in self.db.get_tables():
			self.db.sql('''create table help(
				path text,
				content text,
				title text,
				intro text,
				full_path text,
				fulltext(title),
				fulltext(content))
				COLLATE=utf8mb4_unicode_ci
				ENGINE=MyISAM
				CHARACTER SET=utf8mb4''')

	def search(self, words):
		self.connect()
		return self.db.sql('select title, intro, path from help where match(content) against (%s) limit 10', words)

	def get_content(self, path):
		self.connect()
		query = '''select title, content from help
			where path like "{path}%" order by path desc limit 1'''
		result = None

		if not path.startswith('/user'):
			path = '%' + path

		if not path.endswith('index'):
			result = self.db.sql(query.format(path=os.path.join(path, 'index')))

		if not result:
			result = self.db.sql(query.format(path=path))

		return {'title':result[0][0], 'content':result[0][1]} if result else {}

	def sync_pages(self):
		self.db.sql('truncate help')
		for app in os.listdir('../apps'):
			docs_folder = '../apps/{app}/{app}/docs/user'.format(app=app)
			self.out_base_path = '../apps/{app}/{app}/docs'.format(app=app)
			if os.path.exists(docs_folder):
				for basepath, folders, files in os.walk(docs_folder):
					files = self.reorder_files(files)
					for fname in files:
						if fname.rsplit('.', 1)[-1] in ('md', 'html'):
							fpath = os.path.join(basepath, fname)
							with open(fpath, 'r') as f:
								content = frappe.render_template(unicode(f.read(), 'utf-8'),
									{'docs_base_url': '/assets/{app}_docs'.format(app=app)})

								relpath = self.get_out_path(fpath)
								content = self.make_content(content, fpath)
								title = self.make_title(basepath, fname, content)
								intro = self.make_intro(content)
								self.db.sql('''insert into help(path, content, title, intro, full_path)
									values (%s, %s, %s, %s, %s)''', (relpath, content, title, intro, fpath))


	def make_title(self, basepath, filename, html):
		if '<h1>' in html:
			title = html.split("<h1>", 1)[1].split("</h1>", 1)[0]
		elif 'index' in filename:
			title = basepath.rsplit('/', 1)[-1].title().replace("-", " ")
		else:
			title = filename.rsplit('.', 1)[0].title().replace("-", " ")
		return title

	def make_intro(self, html):
		intro = ""
		if '<p>' in html:
			intro = html.split('<p>', 1)[1].split('</p>', 1)[0]
		if 'Duration' in html:
			intro = "Help Video: " + intro
		return intro

	def make_content(self, content, path):
		html = markdown(content)

		if '{next}' in html:
			html = html.replace('{next}', '')

		target = path.split('/', 3)[-1]
		app_name = path.split('/', 3)[2]
		html += '''
			<div class="page-container">
				<div class="page-content">
				<div class="edit-container text-center">
					<i class="icon icon-smile text-muted"></i>
					<a class="edit text-muted" href="https://github.com/frappe/{app_name}/blob/develop/{target}">
						Improve this page
					</a>
				</div>
				</div>
			</div>'''.format(app_name=app_name, target=target)

		soup = BeautifulSoup(html, 'html.parser')

		for link in soup.find_all('a'):
			if link.has_attr('href'):
				url = link['href']
				if '/user' in url:
					data_path = url[url.index('/user'):]
					if '.' in data_path:
						data_path = data_path[: data_path.rindex('.')]
					if data_path:
						link['data-path'] = data_path

		parent = self.get_parent(path)
		if parent:
			parent_tag = soup.new_tag('a')
			parent_tag.string = parent['title']
			parent_tag['class'] = 'parent-link'
			parent_tag['data-path'] = parent['path']
			soup.find().insert_before(parent_tag)

		return soup.prettify()

	def build_index(self):
		for data in self.db.sql('select path, full_path, content from help'):
			self.make_index(data[0], data[1], data[2])

	def make_index(self, original_path, full_path, content):
		'''Make index from index.txt'''
		if '{index}' in content:
			path = os.path.dirname(full_path)
			files = []

			# get files from index.txt
			index_path = os.path.join(path, "index.txt")
			if os.path.exists(index_path):
				with open(index_path, 'r') as f:
					files = f.read().splitlines()

			# files not in index.txt
			for f in os.listdir(path):
				if not os.path.isdir(os.path.join(path, f)):
					name, extn = f.rsplit('.', 1)
					if name not in files \
						and name != 'index' and extn in ('md', 'html'):
						files.append(name)

			links_html = "<ol class='index-links'>"
			for line in files:
				fpath = os.path.join(os.path.dirname(original_path), line)

				title = self.db.sql('select title from help where path like %s',
					os.path.join(fpath, 'index') + '%')
				if not title:
					title = self.db.sql('select title from help where path like %s',
						fpath + '%')

				if title:
					title = title[0][0]
					links_html += "<li><a data-path='{fpath}'> {title} </a></li>".format(
						fpath=fpath, title=title)
				# else:
				#	bad entries in .txt files
				# 	print fpath

			links_html += "</ol>"
			html = content.replace('{index}', links_html)

			self.db.sql('update help set content=%s where path=%s', (html, original_path))

	def get_out_path(self, path):
		return '/' + os.path.relpath(path, self.out_base_path)

	def get_parent(self, child_path):
		if 'index' in child_path:
			child_path = child_path[: child_path.rindex('index')]
		if child_path[-1] == '/':
			child_path = child_path[:-1]

		parent_path = self.get_out_path(child_path[: child_path.rindex('/')] + "/index")

		out = self.get_content(parent_path)

		if not out:
			return None

		out['path'] = parent_path
		return out

	def reorder_files(self, files):
		pos = 0
		if 'index.md' in files:
			pos = files.index('index.md')
		elif 'index.html' in files:
			pos = files.index('index.html')
		if pos:
			files[0], files[pos] = files[pos], files[0]
		return files