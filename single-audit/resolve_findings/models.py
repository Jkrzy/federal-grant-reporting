from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError


def validate_cfda_number(number):
    # CFDA stands for "Catalog of Federal Domestic Assistance."
    if len(str(number)) != 5:
        raise ValidationError('A CFDA number must have five digits. The number \
                               you provided is %s.' % number)
    # @todo: Determine and add remaining kinds of CFDA number validation, when
    #        appropriate.


# https://docs.djangoproject.com/en/2.2/topics/auth/customizing/#using-a-custom-user-model-when-starting-a-project
class User(AbstractUser):
    pass


class Grantee(models.Model):
    name = models.CharField(max_length=250)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class Agency(models.Model):
    name = models.CharField(max_length=250)
    grantees = models.ManyToManyField(Grantee)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class Grant(models.Model):
    name = models.CharField(max_length=250)
    cfda = models.IntegerField(
        validators=[validate_cfda_number]
    )
    recipient = models.ManyToManyField(Grantee)

    class Meta:
        ordering = ('cfda',)

    def __str__(self):
        return self.name


class NewFindingManager(models.Manager):
    def get_queryset(self):
        return super(NewFindingManager,
                     self).get_queryset()\
                     .filter(status='new')


class Finding(models.Model):
    objects = models.Manager()
    new = NewFindingManager()

    STATUS_TYPE_CHOICES = (
        ('new', 'New'),
        ('in_progress', 'In progress'),
        ('resolved', 'Resolved'),
    )
    FINDING_TYPE_CHOICES = (
        ('material_weakness', 'Material Weakness'),
        ('significant_deficiency', 'Significant Deficiency'),
    )
    name = models.CharField(max_length=250)
    number = models.CharField(max_length=35)
    finding_type = models.CharField(
        max_length=100,
        choices=FINDING_TYPE_CHOICES,
        default='material_weakness'
    )
    condition = models.TextField()
    cause = models.TextField()
    criteria = models.TextField()
    effect = models.TextField()
    recommendation = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=35,
        choices=STATUS_TYPE_CHOICES,
        default='new'
    )
    grantee = models.ForeignKey(
        Grantee,
        related_name='findings',
        on_delete=models.CASCADE,
        null=True
    )
    agencies_affected = models.ManyToManyField(Agency)

    class Meta:
        ordering = ('-status',)

    def get_absolute_url(self):
        return "/finding/%i/" % self.id

    def __str__(self):
        return self.name


class Comment(models.Model):
    finding = models.ForeignKey(
        Finding,
        related_name='comments',
        on_delete=models.CASCADE
    )
    author = models.ForeignKey(
        get_user_model(),
        related_name='comments',
        on_delete=models.CASCADE
    )
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ('created',)

    def __str__(self):
        return 'Comment by {} on {}'.format(self.author, self.finding)
