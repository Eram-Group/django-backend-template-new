"""Notifications v2 schema, ADDITIVE on top of main's 0001-0003.

Broadcast + NotificationDelivery + NotificationKindConfig tables and the
Notification.broadcast link. The legacy ``push_sent_at``/``sms_sent_at``
markers are deliberately still present here: 0005 copies them into
NotificationDelivery rows, then 0006 drops them. (A rewritten 0001 would be
skipped by every database that already applied it.)
"""

import django.db.models.deletion
import django.db.models.functions.datetime
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_alter_device_created_at_alter_device_updated_at_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationDelivery',
            fields=[
                ('id', models.UUIDField(db_default=models.Func(function='uuidv7'), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now(), db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now())),
                ('channel', models.CharField(choices=[('push', 'Push'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20, verbose_name='channel')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('sent', 'Sent'), ('delivered', 'Delivered'), ('read', 'Read'), ('failed', 'Failed'), ('skipped', 'Skipped')], default='pending', max_length=20, verbose_name='status')),
                ('provider', models.CharField(blank=True, max_length=50, verbose_name='provider')),
                ('provider_message_id', models.CharField(blank=True, max_length=255, verbose_name='provider message id')),
                ('detail', models.TextField(blank=True, verbose_name='detail')),
                ('sent_at', models.DateTimeField(blank=True, null=True, verbose_name='sent at')),
                ('attempts', models.PositiveSmallIntegerField(default=0, verbose_name='attempts')),
            ],
            options={
                'verbose_name': 'notification delivery',
                'verbose_name_plural': 'notification deliveries',
            },
        ),
        migrations.CreateModel(
            name='NotificationKindConfig',
            fields=[
                ('id', models.UUIDField(db_default=models.Func(function='uuidv7'), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now(), db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now())),
                ('kind', models.CharField(choices=[('welcome', 'Welcome'), ('announcement', 'Announcement'), ('payment_paid', 'Payment received'), ('wallet_credited', 'Wallet credited')], max_length=50, unique=True, verbose_name='action')),
                ('channels', models.JSONField(blank=True, default=list, verbose_name='channels')),
                ('title', models.CharField(max_length=255, verbose_name='title')),
                ('title_ar', models.CharField(max_length=255, null=True, verbose_name='title')),
                ('title_en', models.CharField(max_length=255, null=True, verbose_name='title')),
                ('body', models.TextField(verbose_name='body')),
                ('body_ar', models.TextField(null=True, verbose_name='body')),
                ('body_en', models.TextField(null=True, verbose_name='body')),
            ],
            options={
                'verbose_name': 'notification action',
                'verbose_name_plural': 'notification actions',
                'ordering': ['kind'],
            },
        ),
        migrations.CreateModel(
            name='Broadcast',
            fields=[
                ('id', models.UUIDField(db_default=models.Func(function='uuidv7'), editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_default=django.db.models.functions.datetime.Now(), db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_default=django.db.models.functions.datetime.Now())),
                ('kind', models.CharField(choices=[('welcome', 'Welcome'), ('announcement', 'Announcement'), ('payment_paid', 'Payment received'), ('wallet_credited', 'Wallet credited')], max_length=50, verbose_name='kind')),
                ('context', models.JSONField(blank=True, default=dict, verbose_name='context')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('dispatching', 'Dispatching'), ('dispatched', 'Dispatched'), ('completed', 'Completed')], default='draft', max_length=20, verbose_name='status')),
                ('language', models.CharField(blank=True, choices=[('ar', 'Arabic'), ('en', 'English')], max_length=2, verbose_name='language filter')),
                ('require_device', models.BooleanField(default=False, help_text='Skip users with no registered device. They would receive an inbox entry but no push.', verbose_name='registered device required')),
                ('joined_after', models.DateField(blank=True, null=True, verbose_name='joined on or after')),
                ('joined_before', models.DateField(blank=True, null=True, verbose_name='joined on or before')),
                ('channels', models.JSONField(blank=True, default=list, verbose_name='channels')),
                ('dispatch_cursor', models.UUIDField(blank=True, null=True, verbose_name='dispatch cursor')),
                ('total_recipients', models.PositiveIntegerField(default=0, verbose_name='total recipients')),
                ('total_deliveries', models.PositiveIntegerField(default=0, verbose_name='total deliveries')),
                ('sent_count', models.PositiveIntegerField(default=0, verbose_name='sent')),
                ('failed_count', models.PositiveIntegerField(default=0, verbose_name='failed')),
                ('skipped_count', models.PositiveIntegerField(default=0, verbose_name='skipped')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='broadcasts_created', to=settings.AUTH_USER_MODEL, verbose_name='created by')),
            ],
            options={
                'verbose_name': 'broadcast',
                'verbose_name_plural': 'broadcasts',
            },
        ),
        migrations.AddField(
            model_name='notification',
            name='broadcast',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='notifications.broadcast', verbose_name='broadcast'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(condition=models.Q(('broadcast__isnull', False)), fields=('broadcast', 'recipient'), name='uniq_notification_broadcast_recipient'),
        ),
        migrations.AddField(
            model_name='notificationdelivery',
            name='broadcast',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='notifications.broadcast', verbose_name='broadcast'),
        ),
        migrations.AddField(
            model_name='notificationdelivery',
            name='notification',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='notifications.notification', verbose_name='notification'),
        ),
        migrations.AddIndex(
            model_name='notificationdelivery',
            index=models.Index(fields=['channel', 'status'], name='notificatio_channel_edd073_idx'),
        ),
        migrations.AddIndex(
            model_name='notificationdelivery',
            index=models.Index(condition=models.Q(('broadcast__isnull', False)), fields=['broadcast', 'status'], name='idx_delivery_broadcast_status'),
        ),
        migrations.AddIndex(
            model_name='notificationdelivery',
            index=models.Index(condition=models.Q(('broadcast__isnull', True), ('status__in', ['pending', 'processing'])), fields=['status', 'created_at'], name='idx_delivery_txn_sweep'),
        ),
        migrations.AddConstraint(
            model_name='notificationdelivery',
            constraint=models.UniqueConstraint(fields=('notification', 'channel'), name='uniq_delivery_notification_channel'),
        ),
        migrations.AddConstraint(
            model_name='notificationdelivery',
            constraint=models.UniqueConstraint(condition=models.Q(('provider_message_id', ''), _negated=True), fields=('provider', 'provider_message_id'), name='uniq_delivery_provider_message_id'),
        ),
    ]
